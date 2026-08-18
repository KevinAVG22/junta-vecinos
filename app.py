from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import Markup, escape
import os
import re
import datetime
from pathlib import Path
import base64
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import io
import openpyxl
import json
import time
import ssl
import threading
import urllib.parse
import urllib.request
import urllib.error

load_dotenv()

MAP_SECTOR_CONTEXT = os.getenv(
    'MAP_SECTOR_CONTEXT',
    'Colón Oriente, Las Condes, Santiago, Chile',
)
# Cuadrante: Cristóbal Colón, Padre Hurtado Sur, Río Guadiana y Paul Harris
MAP_CENTER_LAT = float(os.getenv('MAP_CENTER_LAT', '-33.4143'))
MAP_CENTER_LNG = float(os.getenv('MAP_CENTER_LNG', '-70.5370'))
MAP_BOUNDS_NORTH = float(os.getenv('MAP_BOUNDS_NORTH', '-33.4119'))
MAP_BOUNDS_SOUTH = float(os.getenv('MAP_BOUNDS_SOUTH', '-33.4200'))
MAP_BOUNDS_WEST = float(os.getenv('MAP_BOUNDS_WEST', '-70.5414'))
MAP_BOUNDS_EAST = float(os.getenv('MAP_BOUNDS_EAST', '-70.5328'))
MAP_CUADRANTE_CALLES = (
    'Cristóbal Colón, Padre Hurtado Sur, Río Guadiana y Paul Harris'
)
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
PHOTON_URL = 'https://photon.komoot.io/api/'
NOMINATIM_HEADERS = {
    'User-Agent': 'JuntaDeVecinosColonOriente/1.0 (Las Condes, Chile)',
    'Accept-Language': 'es',
}
_last_geocode_at = 0.0
_last_photon_at = 0.0
_last_nominatim_at = 0.0
_sync_state = {
    'running': False,
    'total': 0,
    'done': 0,
    'ok': 0,
    'fail': 0,
    'last_message': '',
}
_sync_thread = None
_sync_lock = threading.Lock()


def _map_config():
    return {
        'center': {'lat': MAP_CENTER_LAT, 'lng': MAP_CENTER_LNG},
        'bounds': {
            'north': MAP_BOUNDS_NORTH,
            'south': MAP_BOUNDS_SOUTH,
            'west': MAP_BOUNDS_WEST,
            'east': MAP_BOUNDS_EAST,
        },
        'cuadrante': [
            {'lat': MAP_BOUNDS_SOUTH, 'lng': MAP_BOUNDS_WEST},
            {'lat': MAP_BOUNDS_SOUTH, 'lng': MAP_BOUNDS_EAST},
            {'lat': MAP_BOUNDS_NORTH, 'lng': MAP_BOUNDS_EAST},
            {'lat': MAP_BOUNDS_NORTH, 'lng': MAP_BOUNDS_WEST},
        ],
        'sector_context': MAP_SECTOR_CONTEXT,
        'cuadrante_calles': MAP_CUADRANTE_CALLES,
    }

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'tu-clave-secreta-aqui')
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/junta_vecinos'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Evitar que el navegador muestre páginas "viejas" tras cambios directos en DB
@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.template_filter('merge_dicts')
def merge_dicts(*dicts):
    merged = {}
    for d in dicts:
        if d:
            merged.update(d)
    return merged


def _normalize_search_text(value):
    return re.sub(r'[.\-\s]', '', (value or '')).lower()


@app.template_filter('highlight')
def highlight_filter(text, term):
    """Resalta coincidencias de búsqueda dentro del texto."""
    if text is None:
        return ''
    s = str(text)
    t = (term or '').strip()
    if not t:
        return escape(s)

    pattern = re.compile(re.escape(t), re.IGNORECASE)
    if pattern.search(s):
        return _highlight_pattern(s, pattern)

    s_norm = _normalize_search_text(s)
    t_norm = _normalize_search_text(t)
    if t_norm and t_norm in s_norm:
        return Markup(f'<mark class="dt-highlight">{escape(s)}</mark>')
    return escape(s)


def _highlight_pattern(text, pattern):
    parts = []
    last = 0
    for match in pattern.finditer(text):
        parts.append(escape(text[last:match.start()]))
        parts.append(Markup(f'<mark class="dt-highlight">{escape(match.group())}</mark>'))
        last = match.end()
    parts.append(escape(text[last:]))
    return Markup(''.join(parts))


@app.template_filter('matches_term')
def matches_term_filter(text, term):
    if not term or text is None:
        return False
    s = str(text)
    t = str(term).strip()
    if not t:
        return False
    if t.lower() in s.lower():
        return True
    return _normalize_search_text(t) in _normalize_search_text(s)

# Función para validar RUT chileno
def validar_rut(rut):
    """
    Valida un RUT chileno.
    Retorna (True, None) si es válido, (False, mensaje_error) si no lo es.
    """
    # Limpiar el RUT de puntos y guiones
    rut_limpio = re.sub(r'[.-]', '', rut.upper())
    
    # Verificar que tenga al menos 8 dígitos
    if len(rut_limpio) < 8:
        return False, "El RUT debe tener al menos 8 dígitos"
    
    # Separar número y dígito verificador
    numero = rut_limpio[:-1]
    dv = rut_limpio[-1]
    
    # Verificar que el número sea solo dígitos
    if not numero.isdigit():
        return False, "El número del RUT debe contener solo dígitos"
    
    # Verificar que el dígito verificador sea válido
    if dv not in '0123456789K':
        return False, "El dígito verificador debe ser un número o 'K'"
    
    # Calcular dígito verificador
    suma = 0
    multiplicador = 2
    
    for digito in reversed(numero):
        suma += int(digito) * multiplicador
        multiplicador = multiplicador + 1 if multiplicador < 7 else 2
    
    resto = suma % 11
    dv_calculado = 11 - resto
    
    if dv_calculado == 11:
        dv_calculado = '0'
    elif dv_calculado == 10:
        dv_calculado = 'K'
    else:
        dv_calculado = str(dv_calculado)
    
    # Comparar dígito verificador calculado con el ingresado
    if dv != dv_calculado:
        return False, f"El dígito verificador es incorrecto. Debería ser '{dv_calculado}'"
    
    return True, None


def _parse_date_flexible(value: str):
    """
    Acepta YYYY-MM-DD (HTML date) o DD-MM-YYYY / DD/MM/YYYY.
    Retorna datetime.date o None si no se puede parsear.
    """
    v = (value or '').strip()
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(v)
    except Exception:
        pass
    for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(v, fmt).date()
        except Exception:
            continue
    return None


def _arg(name, default=''):
    return (request.args.get(name) or default).strip()


def _apply_ilike(query, column, value):
    if value:
        query = query.filter(column.ilike(f'%{value}%'))
    return query


def _apply_rut_ilike(query, column, value):
    if not value:
        return query
    term = f'%{value}%'
    conditions = [column.ilike(term)]
    rut_clean = re.sub(r'[.\-\s]', '', value)
    if rut_clean:
        normalized = db.func.replace(
            db.func.replace(db.func.replace(column, '.', ''), '-', ''), ' ', ''
        )
        conditions.append(normalized.ilike(f'%{rut_clean}%'))
    return query.filter(db.or_(*conditions))


def _apply_or_ilike(query, columns, value):
    if not value:
        return query
    term = f'%{value}%'
    return query.filter(db.or_(*[col.ilike(term) for col in columns]))


def _order_by_col(query, column, sort_order):
    if sort_order == 'asc':
        return query.order_by(column.asc())
    return query.order_by(column.desc())


def _has_any_filter(*values):
    return any(v for v in values if v)


def _certificados_por_vecino(vecinos):
    if not vecinos:
        return {}
    certs = CertificadoResidencia.query.filter_by(activo=True).order_by(
        CertificadoResidencia.fecha.desc()
    ).all()
    by_rut = {}
    for cert in certs:
        key = _normalize_search_text(cert.rut)
        if key:
            by_rut.setdefault(key, []).append(cert)
    return {
        v.id: by_rut.get(_normalize_search_text(v.rut), [])
        for v in vecinos
    }

# Función para formatear RUT
def formatear_rut(rut):
    """
    Formatea un RUT para mostrarlo con puntos y guión.
    """
    rut_limpio = re.sub(r'[.-]', '', rut.upper())
    if len(rut_limpio) < 8:
        return rut
    
    numero = rut_limpio[:-1]
    dv = rut_limpio[-1]
    
    # Agregar puntos cada 3 dígitos desde la derecha
    numero_formateado = ''
    for i, digito in enumerate(reversed(numero)):
        if i > 0 and i % 3 == 0:
            numero_formateado = '.' + numero_formateado
        numero_formateado = digito + numero_formateado
    
    return f"{numero_formateado}-{dv}"

# Función para verificar si un RUT ya existe
def rut_existe(rut, excluir_id=None):
    """
    Verifica si un RUT ya existe en la base de datos.
    excluir_id: ID del vecino a excluir (para edición)
    """
    rut_limpio = re.sub(r'[.-]', '', rut.upper())
    
    for vecino in Vecino.query.filter_by(activo=True).all():
        if excluir_id and vecino.id == excluir_id:
            continue
        rut_existente_limpio = re.sub(r'[.-]', '', vecino.rut.upper())
        if rut_existente_limpio == rut_limpio:
            return True, vecino
    return False, None

# Modelos de base de datos
class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    es_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(30), default='Asistente')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


def _es_admin(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'es_admin', False)) or (getattr(user, 'role', None) == 'Admin')


def _puede_ver_historial(user) -> bool:
    return _es_admin(user)


@app.route('/usuarios')
@login_required
def usuarios():
    if not _es_admin(current_user):
        flash('No tienes permisos para administrar usuarios.', 'error')
        return redirect(url_for('dashboard'))

    f_username = _arg('f_username')
    f_email = _arg('f_email')
    f_role = _arg('f_role')

    query = Usuario.query
    query = _apply_ilike(query, Usuario.username, f_username)
    query = _apply_ilike(query, Usuario.email, f_email)
    if f_role:
        query = query.filter(Usuario.role == f_role)

    usuarios_list = query.order_by(Usuario.id.asc()).all()
    roles = ['Admin', 'Presidente', 'Vicepresidente', 'Asistente']
    has_filters = _has_any_filter(f_username, f_email, f_role)
    return render_template(
        'usuarios.html',
        usuarios=usuarios_list,
        roles=roles,
        f_username=f_username,
        f_email=f_email,
        f_role=f_role,
        has_filters=has_filters,
    )


@app.route('/usuarios/<int:id>/rol', methods=['POST'])
@login_required
def actualizar_rol_usuario(id):
    if not _es_admin(current_user):
        flash('No tienes permisos para administrar usuarios.', 'error')
        return redirect(url_for('dashboard'))

    user = Usuario.query.get_or_404(id)
    nuevo_rol = (request.form.get('role') or '').strip()
    roles_validos = {'Admin', 'Presidente', 'Vicepresidente', 'Asistente'}
    if nuevo_rol not in roles_validos:
        flash('Rol inválido.', 'error')
        return redirect(url_for('usuarios'))

    # Evitar que te quites admin a ti mismo por accidente
    if user.id == current_user.id and nuevo_rol != 'Admin':
        flash('No puedes quitarte el rol Admin a ti mismo.', 'error')
        return redirect(url_for('usuarios'))

    user.role = nuevo_rol
    user.es_admin = (nuevo_rol == 'Admin')
    db.session.commit()

    _registrar_movimiento(
        entidad='usuario',
        entidad_id=user.id,
        accion='editar',
        detalles=f"Actualizó rol de usuario: {user.username} -> {nuevo_rol}"
    )

    flash('Rol actualizado exitosamente.', 'success')
    return redirect(url_for('usuarios'))

@app.route('/usuarios/<int:id>/reset-password', methods=['POST'])
@login_required
def reset_password_usuario(id):
    if not _es_admin(current_user):
        flash('No tienes permisos para administrar usuarios.', 'error')
        return redirect(url_for('dashboard'))

    user = Usuario.query.get_or_404(id)
    nueva = request.form.get('password_nueva') or ''
    nueva = nueva.strip()

    if len(nueva) < 6:
        flash('La nueva contraseña debe tener al menos 6 caracteres.', 'error')
        return redirect(url_for('usuarios'))

    user.set_password(nueva)
    db.session.commit()

    _registrar_movimiento(
        entidad='usuario',
        entidad_id=user.id,
        accion='editar',
        detalles=f"Reseteó contraseña de usuario: {user.username}"
    )

    flash(f'Contraseña reseteada para {user.username}.', 'success')
    return redirect(url_for('usuarios'))


@app.route('/usuarios/nuevo', methods=['POST'])
@login_required
def crear_usuario():
    if not _es_admin(current_user):
        flash('No tienes permisos para administrar usuarios.', 'error')
        return redirect(url_for('dashboard'))

    username = (request.form.get('username') or '').strip()
    email = (request.form.get('email') or '').strip()
    password = request.form.get('password') or ''
    role = (request.form.get('role') or 'Asistente').strip()

    roles_validos = {'Admin', 'Presidente', 'Vicepresidente', 'Asistente'}
    if role not in roles_validos:
        flash('Rol inválido.', 'error')
        return redirect(url_for('usuarios'))

    if not username or not email or not password:
        flash('Usuario, email y contraseña son obligatorios.', 'error')
        return redirect(url_for('usuarios'))

    if len(password) < 6:
        flash('La contraseña debe tener al menos 6 caracteres.', 'error')
        return redirect(url_for('usuarios'))

    if Usuario.query.filter_by(username=username).first():
        flash('Ese nombre de usuario ya existe.', 'error')
        return redirect(url_for('usuarios'))
    if Usuario.query.filter_by(email=email).first():
        flash('Ese email ya existe.', 'error')
        return redirect(url_for('usuarios'))

    u = Usuario(username=username, email=email, role=role, es_admin=(role == 'Admin'))
    u.set_password(password)
    db.session.add(u)
    db.session.commit()

    _registrar_movimiento(
        entidad='usuario',
        entidad_id=u.id,
        accion='crear',
        detalles=f"Creó usuario: {u.username} ({u.email}) rol={u.role}"
    )

    flash('Usuario creado exitosamente.', 'success')
    return redirect(url_for('usuarios'))


@app.route('/mi-cuenta', methods=['GET', 'POST'])
@login_required
def mi_cuenta():
    if request.method == 'POST':
        actual = request.form.get('password_actual') or ''
        nueva = request.form.get('password_nueva') or ''
        repetir = request.form.get('password_repetir') or ''

        if not current_user.check_password(actual):
            flash('La contraseña actual no es correcta.', 'error')
            return render_template('mi_cuenta.html')

        if len(nueva) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'error')
            return render_template('mi_cuenta.html')

        if nueva != repetir:
            flash('La nueva contraseña no coincide.', 'error')
            return render_template('mi_cuenta.html')

        user = Usuario.query.get(current_user.id)
        user.set_password(nueva)
        db.session.commit()

        _registrar_movimiento(
            entidad='usuario',
            entidad_id=user.id,
            accion='editar',
            detalles=f"Cambió su contraseña: {user.username}"
        )

        flash('Contraseña actualizada exitosamente.', 'success')
        return redirect(url_for('mi_cuenta'))

    return render_template('mi_cuenta.html')


def _calcular_edad(fecha_nacimiento, referencia=None):
    if not fecha_nacimiento:
        return None
    hoy = referencia or datetime.date.today()
    edad = hoy.year - fecha_nacimiento.year
    if (hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day):
        edad -= 1
    return edad


def _rango_etario(edad):
    if edad is None:
        return 'Sin dato'
    if edad < 18:
        return '0-17'
    if edad < 30:
        return '18-29'
    if edad < 45:
        return '30-44'
    if edad < 60:
        return '45-59'
    if edad < 75:
        return '60-74'
    return '75+'


def _parse_fecha_nacimiento(value):
    raw = (value or '').strip()
    if not raw:
        return None, None
    fecha = _parse_date_flexible(raw)
    if fecha is None:
        return None, 'Formato de fecha de nacimiento inválido.'
    if fecha > datetime.date.today():
        return None, 'La fecha de nacimiento no puede ser futura.'
    return fecha, None


def _stats_rangos_etarios(vecinos):
    orden = ('0-17', '18-29', '30-44', '45-59', '60-74', '75+', 'Sin dato')
    conteo = {r: 0 for r in orden}
    for vecino in vecinos:
        conteo[_rango_etario(_calcular_edad(vecino.fecha_nacimiento))] += 1
    return [(r, conteo[r]) for r in orden if conteo[r] > 0]


class Vecino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(100), nullable=False)
    telefono = db.Column(db.String(20))
    domicilio = db.Column(db.String(200), nullable=False)
    rut = db.Column(db.String(20), unique=True, nullable=False)
    fecha_nacimiento = db.Column(db.Date, nullable=True)
    fecha_registro = db.Column(db.DateTime, default=db.func.current_timestamp())
    notas = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)
    latitud = db.Column(db.Float, nullable=True)
    longitud = db.Column(db.Float, nullable=True)
    geocodificado_en = db.Column(db.DateTime, nullable=True)
    geocodificacion_error = db.Column(db.String(255), nullable=True)
    domicilio_mapeado = db.Column(db.String(200), nullable=True)

    @property
    def edad(self):
        return _calcular_edad(self.fecha_nacimiento)

    @property
    def rango_etario(self):
        return _rango_etario(self.edad)


def _normalize_domicilio_key(domicilio):
    return _normalize_search_text(domicilio)


def _rate_limit_geocode():
    _rate_limit_nominatim()


def _rate_limit_photon():
    global _last_photon_at
    elapsed = time.time() - _last_photon_at
    if elapsed < 0.35:
        time.sleep(0.35 - elapsed)
    _last_photon_at = time.time()


def _rate_limit_nominatim():
    global _last_nominatim_at
    elapsed = time.time() - _last_nominatim_at
    if elapsed < 1.25:
        time.sleep(1.25 - elapsed)
    _last_nominatim_at = time.time()


_DOMICILIO_CORRECCIONES = (
    (re.compile(r'\bcierra\s+nevada\b', re.I), 'Sierra Nevada'),
    (re.compile(r'\bsierra\s+nevada\b', re.I), 'Sierra Nevada'),
    (re.compile(r'\brio\s+guadiana\b', re.I), 'Río Guadiana'),
    (re.compile(r'\bleon\s+negro\b', re.I), 'León Negro'),
    (re.compile(r'\bloma\s+larga\b', re.I), 'Loma Larga'),
    (re.compile(r'\bcerro\s+altar\b', re.I), 'Cerro Altar'),
    (re.compile(r'\bpaul\s+harris\b', re.I), 'Paul Harris'),
    (re.compile(r'\bcristobal\s+colon\b', re.I), 'Cristóbal Colón'),
    (re.compile(r'\bpadre\s+hurtado\b', re.I), 'Padre Hurtado Sur'),
)


def _normalizar_domicilio_geocodificacion(domicilio):
    dom = re.sub(r'\s+', ' ', (domicilio or '').strip())
    if not dom:
        return dom
    for pattern, repl in _DOMICILIO_CORRECCIONES:
        dom = pattern.sub(repl, dom)
    return dom


def _coords_dentro_cuadrante(lat, lng):
    if lat is None or lng is None:
        return False
    return (
        MAP_BOUNDS_SOUTH <= lat <= MAP_BOUNDS_NORTH
        and MAP_BOUNDS_WEST <= lng <= MAP_BOUNDS_EAST
    )


def _tiene_ubicacion_mapa(vecino):
    return vecino.latitud is not None and vecino.longitud is not None


def _vecino_necesita_geocodificacion(vecino):
    dom = (vecino.domicilio or '').strip()
    if not dom:
        return False
    if not _tiene_ubicacion_mapa(vecino):
        return True
    mapeado = (vecino.domicilio_mapeado or '').strip()
    if mapeado != _normalizar_domicilio_geocodificacion(dom):
        return True
    return False


def _extraer_calle_numero(domicilio):
    dom = _normalizar_domicilio_geocodificacion(domicilio)
    m = re.match(r'^(?P<calle>.+?)\s+(?P<numero>\d+\S*)$', dom)
    if m:
        return m.group('calle').strip(), m.group('numero').strip()
    return dom, None


def _calles_coinciden(calle_buscada, calle_resultado):
    a = _normalize_search_text(calle_buscada or '')
    b = _normalize_search_text(calle_resultado or '')
    if not a or not b:
        return False
    return a in b or b in a


def _puntuar_candidato_geocodificacion(
    calle_buscada,
    numero_buscado,
    lat,
    lon,
    *,
    housenumber=None,
    street=None,
    name=None,
    osm_type=None,
    osm_class=None,
):
    score = 0
    street_text = ' '.join(part for part in [street, name] if part).strip()
    if _calles_coinciden(calle_buscada, street_text):
        score += 25
    if numero_buscado and housenumber:
        hn = str(housenumber).strip()
        nb = str(numero_buscado).strip()
        if hn == nb:
            score += 120
        elif nb in hn:
            score += 50
    elif numero_buscado and not housenumber:
        score -= 20
    if _coords_dentro_cuadrante(lat, lon):
        score += 15
    if osm_type in {'house', 'building', 'residential', 'address'}:
        score += 30
    elif osm_class == 'building':
        score += 25
    elif osm_class == 'highway':
        score -= 15
    return score


def _queries_geocodificacion(domicilio):
    dom = _normalizar_domicilio_geocodificacion(domicilio)
    calle, numero = _extraer_calle_numero(dom)
    queries = []
    if calle and numero:
        queries.extend([
            f"{numero} {calle}, Colón Oriente, Las Condes, Chile",
            f"Pasaje {calle} {numero}, Colón Oriente, Las Condes, Chile",
            f"{numero}, Pasaje {calle}, Colón Oriente, Las Condes, Chile",
            f"{numero} {calle}, Las Condes, Chile",
        ])
    queries.extend([
        f"{dom}, Las Condes, Chile",
        f"{dom}, {MAP_SECTOR_CONTEXT}",
        f"{dom}, Colón Oriente, Las Condes, Chile",
    ])
    sin_num = re.sub(r'\s+\d+\s*$', '', dom).strip()
    if sin_num and sin_num.lower() != dom.lower():
        queries.append(f"{sin_num}, Las Condes, Chile")
    queries.append(dom)
    # Eliminar duplicados preservando orden
    seen = set()
    unique = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique


def _http_get_json(url, headers, retries=3):
    ctx = ssl.create_default_context()
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in {429, 503} and attempt < retries - 1:
                time.sleep(min(30, 5 * (2 ** attempt)))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise last_exc
    return None


def _nominatim_buscar(query, viewbox=None, bounded=False, limit=8):
    params = {
        'q': query,
        'format': 'json',
        'limit': limit,
        'countrycodes': 'cl',
        'addressdetails': '1',
    }
    if viewbox:
        params['viewbox'] = viewbox
    if bounded:
        params['bounded'] = '1'
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    return _http_get_json(url, NOMINATIM_HEADERS)


def _nominatim_estructurado(calle, numero):
    params = {
        'street': f'{numero} {calle}',
        'suburb': 'Colón Oriente',
        'city': 'Las Condes',
        'country': 'Chile',
        'format': 'json',
        'limit': 8,
        'addressdetails': '1',
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    return _http_get_json(url, NOMINATIM_HEADERS)


def _photon_buscar(query, calle_buscada=None, numero_buscado=None, limit=10):
    params = urllib.parse.urlencode({'q': query, 'limit': limit})
    url = f"{PHOTON_URL}?{params}"
    data = _http_get_json(
        url,
        headers={
            'User-Agent': NOMINATIM_HEADERS['User-Agent'],
            'Accept': 'application/json',
        },
    )

    candidatos = []
    for feature in data.get('features', []):
        props = feature.get('properties') or {}
        if (props.get('countrycode') or '').upper() != 'CL':
            continue
        coords = (feature.get('geometry') or {}).get('coordinates') or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        street_text = props.get('street') or props.get('name') or ''
        score = _puntuar_candidato_geocodificacion(
            calle_buscada or '',
            numero_buscado,
            lat,
            lon,
            housenumber=props.get('housenumber'),
            street=street_text,
            name=props.get('name'),
            osm_type=props.get('osm_value'),
            osm_class=props.get('osm_key'),
        )
        exacta = bool(
            numero_buscado
            and props.get('housenumber')
            and str(props.get('housenumber')).strip() == str(numero_buscado).strip()
        )
        candidatos.append((score, lat, lon, exacta))
    candidatos.sort(key=lambda item: -item[0])
    return candidatos


def _seleccionar_resultado_nominatim(results, calle_buscada=None, numero_buscado=None):
    if not results:
        return None, None, False
    candidatos = []
    for item in results:
        lat = float(item['lat'])
        lon = float(item['lon'])
        address = item.get('address') or {}
        score = _puntuar_candidato_geocodificacion(
            calle_buscada or '',
            numero_buscado,
            lat,
            lon,
            housenumber=address.get('house_number'),
            street=address.get('road') or address.get('pedestrian') or address.get('living_street'),
            name=address.get('suburb') or address.get('neighbourhood'),
            osm_type=item.get('type'),
            osm_class=item.get('class'),
        )
        exacta = bool(
            numero_buscado
            and address.get('house_number')
            and str(address.get('house_number')).strip() == str(numero_buscado).strip()
        )
        candidatos.append((score, lat, lon, exacta))
    candidatos.sort(key=lambda row: -row[0])
    _, lat, lon, exacta = candidatos[0]
    return lat, lon, exacta


def _resultado_geocodificacion(lat, lon, exacta, calle_buscada, numero_buscado):
    if lat is None or lon is None:
        return None, None, 'Sin resultados de geocodificación', False
    if _coords_dentro_cuadrante(lat, lon):
        if exacta:
            return lat, lon, None, True
        if numero_buscado:
            return lat, lon, 'Ubicación aproximada en la calle (número no disponible en el mapa)', False
        return lat, lon, None, False
    aviso = 'Ubicación fuera del cuadrante del sector'
    if not exacta and numero_buscado:
        aviso = 'Ubicación aproximada fuera del cuadrante del sector'
    return lat, lon, aviso, exacta


def _geocodificar_domicilio(domicilio):
    domicilio = _normalizar_domicilio_geocodificacion(domicilio)
    if not domicilio:
        return None, None, 'Domicilio vacío', False

    calle, numero = _extraer_calle_numero(domicilio)
    queries = _queries_geocodificacion(domicilio)
    return _geocodificar_domicilio_osm(domicilio, calle, numero, queries)


def _geocodificar_domicilio_osm(domicilio, calle, numero, queries):
    viewbox = f"{MAP_BOUNDS_WEST},{MAP_BOUNDS_NORTH},{MAP_BOUNDS_EAST},{MAP_BOUNDS_SOUTH}"
    last_error = 'Sin resultados de geocodificación'
    mejor = None

    def _evaluar_candidatos(candidatos):
        nonlocal mejor
        for score, lat, lon, exacta in candidatos:
            if score <= 0:
                continue
            if mejor is None or score > mejor[0] or (score == mejor[0] and exacta and not mejor[3]):
                mejor = (score, lat, lon, exacta)

    if calle and numero:
        _rate_limit_nominatim()
        try:
            data = _nominatim_estructurado(calle, numero)
            if data:
                lat, lon, exacta = _seleccionar_resultado_nominatim(
                    data, calle_buscada=calle, numero_buscado=numero
                )
                if lat is not None:
                    return _resultado_geocodificacion(lat, lon, exacta, calle, numero)
        except Exception as exc:
            last_error = str(exc) or 'Error en búsqueda estructurada'

    for query in queries[:4]:
        _rate_limit_photon()
        try:
            candidatos = _photon_buscar(query, calle_buscada=calle, numero_buscado=numero)
            _evaluar_candidatos(candidatos)
        except Exception as exc:
            last_error = str(exc) or 'Error en geocodificación alternativa'

    estrategias = [
        {'viewbox': viewbox, 'bounded': False},
        {'viewbox': None, 'bounded': False},
    ]

    for query in queries[:3]:
        for strategy in estrategias:
            _rate_limit_nominatim()
            try:
                data = _nominatim_buscar(
                    query,
                    viewbox=strategy['viewbox'],
                    bounded=strategy['bounded'],
                )
                if data:
                    lat, lon, exacta = _seleccionar_resultado_nominatim(
                        data, calle_buscada=calle, numero_buscado=numero
                    )
                    if lat is not None:
                        score = _puntuar_candidato_geocodificacion(
                            calle, numero, lat, lon,
                            housenumber=numero if exacta else None,
                            street=calle,
                        )
                        if mejor is None or score > mejor[0]:
                            mejor = (score, lat, lon, exacta)
            except urllib.error.HTTPError as exc:
                last_error = f'Servicio de mapas respondió HTTP {exc.code}'
                if exc.code in {403, 429}:
                    break
            except Exception as exc:
                last_error = str(exc) or 'Error al consultar el servicio de mapas'

    if mejor:
        _, lat, lon, exacta = mejor
        return _resultado_geocodificacion(lat, lon, exacta, calle, numero)

    return None, None, last_error, False


def _parse_coord(value):
    if value is None:
        return None
    s = str(value).strip().replace(',', '.')
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _aplicar_ubicacion_vecino(vecino, lat_str=None, lng_str=None, geocodificar_si_falta=True):
    lat = _parse_coord(lat_str)
    lng = _parse_coord(lng_str)
    dom = (vecino.domicilio or '').strip()
    if lat is not None and lng is not None:
        vecino.latitud = lat
        vecino.longitud = lng
        vecino.geocodificado_en = datetime.datetime.utcnow()
        vecino.domicilio_mapeado = _normalizar_domicilio_geocodificacion(dom)
        if _coords_dentro_cuadrante(lat, lng):
            vecino.geocodificacion_error = None
        else:
            vecino.geocodificacion_error = 'Coordenadas fuera del cuadrante del sector'
        return True
    if geocodificar_si_falta and dom:
        return _geocodificar_vecino(vecino)
    return False


def _form_vecino_desde_request():
    return {
        'nombre': request.form['nombre'].strip(),
        'apellidos': request.form['apellidos'].strip(),
        'telefono': request.form['telefono'].strip(),
        'domicilio': request.form['domicilio'].strip(),
        'rut': request.form['rut'].strip(),
        'fecha_nacimiento': request.form.get('fecha_nacimiento', '').strip(),
        'notas': request.form['notas'].strip(),
        'latitud': request.form.get('latitud', '').strip(),
        'longitud': request.form.get('longitud', '').strip(),
    }


def _geocodificar_vecino(vecino):
    dom = (vecino.domicilio or '').strip()
    lat, lon, err, exacta = _geocodificar_domicilio(dom)
    vecino.geocodificado_en = datetime.datetime.utcnow()
    if lat is not None and lon is not None:
        vecino.latitud = lat
        vecino.longitud = lon
        vecino.domicilio_mapeado = _normalizar_domicilio_geocodificacion(dom)
        if err:
            vecino.geocodificacion_error = err
        elif _coords_dentro_cuadrante(lat, lon):
            vecino.geocodificacion_error = None
        else:
            vecino.geocodificacion_error = 'Ubicación fuera del cuadrante del sector'
        return True
    vecino.latitud = None
    vecino.longitud = None
    vecino.geocodificacion_error = err or 'No se pudo geocodificar la dirección'
    return False


def _vecinos_pendientes_ubicacion():
    return [
        v for v in Vecino.query.filter_by(activo=True).order_by(Vecino.id.asc()).all()
        if _vecino_necesita_geocodificacion(v)
    ]


def _sincronizar_ubicaciones_vecinos(force=False):
    global _sync_state
    if force:
        pendiente_ids = [
            v.id for v in Vecino.query.filter_by(activo=True).order_by(Vecino.id.asc()).all()
            if (v.domicilio or '').strip()
        ]
    else:
        pendiente_ids = [v.id for v in _vecinos_pendientes_ubicacion()]

    _sync_state.update({
        'total': len(pendiente_ids),
        'done': 0,
        'ok': 0,
        'fail': 0,
    })
    try:
        for idx, vecino_id in enumerate(pendiente_ids, start=1):
            vecino = db.session.get(Vecino, vecino_id)
            if not vecino or not vecino.activo:
                continue
            if _geocodificar_vecino(vecino):
                _sync_state['ok'] += 1
            else:
                _sync_state['fail'] += 1
            _sync_state['done'] = idx
            db.session.commit()
        _sync_state['last_message'] = (
            f"Listo: {_sync_state['ok']} ubicados, {_sync_state['fail']} sin ubicación."
        )
    except Exception as exc:
        db.session.rollback()
        _sync_state['last_message'] = str(exc)
        raise
    finally:
        _sync_state['running'] = False
    return dict(_sync_state)


def _iniciar_sincronizacion_ubicaciones(force=False):
    global _sync_thread
    with _sync_lock:
        if _sync_state['running']:
            return False
        if force:
            total = Vecino.query.filter_by(activo=True).filter(
                Vecino.domicilio.isnot(None), Vecino.domicilio != ''
            ).count()
            if not total:
                _sync_state['last_message'] = 'No hay vecinos con domicilio para sincronizar.'
                return False
        else:
            pendientes = _vecinos_pendientes_ubicacion()
            if not pendientes:
                _sync_state['last_message'] = 'Todos los vecinos ya tienen ubicación guardada.'
                return False
            total = len(pendientes)
        _sync_state.update({
            'running': True,
            'total': total,
            'done': 0,
            'ok': 0,
            'fail': 0,
            'last_message': f'Sincronizando {total} vecino(s)...',
        })

        def worker():
            with app.app_context():
                try:
                    _sincronizar_ubicaciones_vecinos(force=force)
                except Exception:
                    db.session.rollback()

        _sync_thread = threading.Thread(target=worker, daemon=True, name='sync-ubicaciones-vecinos')
        _sync_thread.start()
    return True


def _estado_sincronizacion_mapa():
    pendientes = len(_vecinos_pendientes_ubicacion())
    return {
        **_sync_state,
        'pendientes': pendientes,
    }


def _clave_grupo_marcador(vecino):
    domicilio_key = _normalize_domicilio_key(vecino.domicilio)
    if not domicilio_key:
        return None
    lat_key = round(float(vecino.latitud), 4)
    lng_key = round(float(vecino.longitud), 4)
    return f'{domicilio_key}|{lat_key}|{lng_key}'


def _marcadores_mapa_desde_vecinos(vecinos):
    grupos = {}
    for vecino in vecinos:
        if not _tiene_ubicacion_mapa(vecino):
            continue
        key = _clave_grupo_marcador(vecino)
        if not key:
            continue
        if key not in grupos:
            grupos[key] = {
                'domicilio': vecino.domicilio,
                'lat': vecino.latitud,
                'lng': vecino.longitud,
                'vecinos': [],
            }
        grupos[key]['vecinos'].append({
            'id': vecino.id,
            'nombre': vecino.nombre,
            'apellidos': vecino.apellidos,
            'rut': vecino.rut,
            'telefono': vecino.telefono or '',
        })

    marcadores = []
    for grupo in grupos.values():
        marcadores.append({
            **grupo,
            'count': len(grupo['vecinos']),
        })
    marcadores.sort(key=lambda m: (-m['count'], m['domicilio'].lower()))
    return marcadores


def _stats_mapa(vecinos):
    con_ubicacion = [v for v in vecinos if _tiene_ubicacion_mapa(v)]
    en_cuadrante = [v for v in con_ubicacion if _coords_dentro_cuadrante(v.latitud, v.longitud)]
    marcadores = _marcadores_mapa_desde_vecinos(con_ubicacion)
    return {
        'total_vecinos': len(vecinos),
        'vecinos_con_ubicacion': len(con_ubicacion),
        'vecinos_en_cuadrante': len(en_cuadrante),
        'pines_activos': len(marcadores),
        'sin_ubicacion': len(vecinos) - len(con_ubicacion),
    }


def _apply_vecino_search(query, q):
    if not q:
        return query
    term = f'%{q}%'
    conditions = [
        Vecino.nombre.ilike(term),
        Vecino.apellidos.ilike(term),
        Vecino.domicilio.ilike(term),
        Vecino.telefono.ilike(term),
        Vecino.rut.ilike(term),
        Vecino.notas.ilike(term),
    ]
    rut_clean = re.sub(r'[.\-\s]', '', q)
    if rut_clean:
        normalized = db.func.replace(
            db.func.replace(db.func.replace(Vecino.rut, '.', ''), '-', ''), ' ', ''
        )
        conditions.append(normalized.ilike(f'%{rut_clean}%'))
    return query.filter(db.or_(*conditions))


class SimplePagination:
    def __init__(self, page, per_page, total):
        self.page = max(1, page or 1)
        self.per_page = per_page
        self.total = total
        self.pages = max(1, (total + per_page - 1) // per_page) if total else 1
        if self.page > self.pages:
            self.page = self.pages
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1 if self.has_prev else None
        self.next_num = self.page + 1 if self.has_next else None
        self.items = []

    def iter_pages(self, left_edge=1, left_current=2, right_current=2, right_edge=1):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (
                    num > self.page - left_current - 1
                    and num < self.page + right_current
                )
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


def _paginate_list(items, page, per_page):
    pagination = SimplePagination(page, per_page, len(items))
    start = (pagination.page - 1) * per_page
    pagination.items = items[start:start + per_page]
    return pagination


def _agrupar_vecinos_por_casa(vecinos):
    grupos = {}
    for vecino in vecinos:
        domicilio = (vecino.domicilio or '').strip()
        key = _normalize_domicilio_key(domicilio)
        if not key:
            continue
        if key not in grupos:
            grupos[key] = {
                'domicilio_key': key,
                'domicilio': domicilio,
                'integrantes': [],
            }
        grupos[key]['integrantes'].append(vecino)

    casas = []
    for grupo in grupos.values():
        grupo['integrantes'].sort(key=lambda v: (v.apellidos.lower(), v.nombre.lower()))
        domicilios = [(v.domicilio or '').strip() for v in grupo['integrantes']]
        grupo['domicilio'] = max(set(domicilios), key=domicilios.count)
        calle, numero = _extraer_calle_numero(grupo['domicilio'])
        grupo['calle'] = calle or ''
        grupo['numero'] = numero or ''
        grupo['count'] = len(grupo['integrantes'])
        grupo['con_ubicacion'] = any(_tiene_ubicacion_mapa(v) for v in grupo['integrantes'])
        grupo['tipo'] = 'Individual' if grupo['count'] == 1 else 'Compartida'
        casas.append(grupo)
    return casas


def _casa_coincide_busqueda(casa, q):
    if not q:
        return True
    if _matches_term(casa['domicilio'], q):
        return True
    if _matches_term(casa['calle'], q):
        return True
    for vecino in casa['integrantes']:
        if _matches_term(f"{vecino.nombre} {vecino.apellidos}", q):
            return True
        if _matches_term(vecino.rut, q):
            return True
    return False


def _matches_term(text, term):
    if not term or text is None:
        return False
    s = str(text)
    t = str(term).strip()
    if not t:
        return False
    if t.lower() in s.lower():
        return True
    return _normalize_search_text(t) in _normalize_search_text(s)


def _filtrar_casas(casas, q=''):
    if not q:
        return casas
    return [casa for casa in casas if _casa_coincide_busqueda(casa, q)]


def _ordenar_casas(casas, sort_by, sort_order):
    reverse = sort_order == 'desc'

    def sort_key(casa):
        if sort_by == 'integrantes':
            return casa['count']
        if sort_by == 'tipo':
            return (0 if casa['count'] == 1 else 1, casa['domicilio'].lower())
        if sort_by == 'mapa':
            return (1 if casa['con_ubicacion'] else 0, casa['domicilio'].lower())
        return casa['domicilio'].lower()

    return sorted(casas, key=sort_key, reverse=reverse)


def _stats_casas(casas):
    if not casas:
        return {
            'total_casas': 0,
            'total_integrantes': 0,
            'promedio_integrantes': 0,
            'max_integrantes': 0,
            'casa_mas_habitada': '',
            'casas_un_integrante': 0,
            'casas_multiples': 0,
        }
    total_integrantes = sum(c['count'] for c in casas)
    total_casas = len(casas)
    max_casa = max(casas, key=lambda c: c['count'])
    return {
        'total_casas': total_casas,
        'total_integrantes': total_integrantes,
        'promedio_integrantes': round(total_integrantes / total_casas, 1),
        'max_integrantes': max_casa['count'],
        'casa_mas_habitada': max_casa['domicilio'],
        'casas_un_integrante': sum(1 for c in casas if c['count'] == 1),
        'casas_multiples': sum(1 for c in casas if c['count'] > 1),
    }


class RegistroAccion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    usuario_nombre = db.Column(db.String(80), nullable=False)
    vecino_id = db.Column(db.Integer, db.ForeignKey('vecino.id'), nullable=False)
    accion = db.Column(db.String(20), nullable=False)  # 'crear', 'editar', 'eliminar', 'ver'
    fecha_hora = db.Column(db.DateTime, default=db.func.current_timestamp())
    detalles = db.Column(db.Text)


class CertificadoResidencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False)
    nombres = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(100), nullable=False)
    rut = db.Column(db.String(20), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    presentado_en = db.Column(db.String(200))
    pago = db.Column(db.Boolean, default=False)
    archivo_nombre = db.Column(db.String(255))
    archivo_ruta = db.Column(db.String(500))
    documento_id = db.Column(db.Integer)
    activo = db.Column(db.Boolean, default=True)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())


class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    archivo_nombre = db.Column(db.String(255), nullable=False)
    archivo_ruta = db.Column(db.String(500), nullable=False)
    activo = db.Column(db.Boolean, default=True)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

class DocumentoTipo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

class RegistroMovimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    usuario_nombre = db.Column(db.String(80), nullable=False)
    entidad = db.Column(db.String(30), nullable=False)  # 'vecino' | 'certificado' | 'documento' | 'tipo_documento' | 'usuario'
    entidad_id = db.Column(db.Integer, nullable=False)
    accion = db.Column(db.String(20), nullable=False)  # 'crear' | 'editar' | 'eliminar' | 'ver' | 'descargar'
    fecha_hora = db.Column(db.DateTime, default=db.func.current_timestamp())
    detalles = db.Column(db.Text)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

# Rutas
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = Usuario.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            flash('¡Inicio de sesión exitoso!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    q = _arg('q')
    sort_by = request.args.get('sort_by', 'nombre')
    sort_order = request.args.get('sort_order', 'asc')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = Vecino.query.filter_by(activo=True)
    query = _apply_vecino_search(query, q)

    sort_col = {
        'nombre': Vecino.nombre,
        'apellidos': Vecino.apellidos,
        'rut': Vecino.rut,
        'domicilio': Vecino.domicilio,
        'telefono': Vecino.telefono,
        'fecha_nacimiento': Vecino.fecha_nacimiento,
        'fecha_registro': Vecino.fecha_registro,
    }.get(sort_by, Vecino.nombre)
    query = _order_by_col(query, sort_col, sort_order)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    vecinos = pagination.items
    certificados_por_vecino = _certificados_por_vecino(vecinos)

    total_vecinos = Vecino.query.filter_by(activo=True).count()
    vecinos_filtrados = query.count()
    has_filters = bool(q)
    page_params = {
        'q': q,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }
    todos_vecinos = Vecino.query.filter_by(activo=True).all()
    rangos_etarios = _stats_rangos_etarios(todos_vecinos)
    vecinos_con_edad = sum(1 for v in todos_vecinos if v.fecha_nacimiento)

    return render_template(
        'dashboard.html',
        vecinos=vecinos,
        pagination=pagination,
        sort_by=sort_by,
        sort_order=sort_order,
        total_vecinos=total_vecinos,
        vecinos_filtrados=vecinos_filtrados,
        q=q,
        has_filters=has_filters,
        page_params=page_params,
        certificados_por_vecino=certificados_por_vecino,
        rangos_etarios=rangos_etarios,
        vecinos_con_edad=vecinos_con_edad,
    )


@app.route('/casas')
@login_required
def casas_sector():
    q = _arg('q')
    sort_by = request.args.get('sort_by', 'domicilio')
    sort_order = request.args.get('sort_order', 'asc')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    vecinos = Vecino.query.filter_by(activo=True).order_by(Vecino.domicilio.asc()).all()
    todas_casas = _agrupar_vecinos_por_casa(vecinos)
    stats_totales = _stats_casas(todas_casas)

    casas = _filtrar_casas(todas_casas, q=q)
    casas = _ordenar_casas(casas, sort_by, sort_order)
    stats_filtradas = _stats_casas(casas)

    pagination = _paginate_list(casas, page, per_page)
    has_filters = bool(q)
    page_params = {
        'q': q,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }

    return render_template(
        'casas.html',
        casas=pagination.items,
        pagination=pagination,
        sort_by=sort_by,
        sort_order=sort_order,
        q=q,
        has_filters=has_filters,
        page_params=page_params,
        stats_totales=stats_totales,
        stats_filtradas=stats_filtradas,
        casas_filtradas=len(casas),
    )


@app.route('/mapa')
@login_required
def mapa_sector():
    cfg = _map_config()
    return render_template(
        'mapa_sector.html',
        map_config=cfg,
    )


@app.route('/api/mapa/datos')
@login_required
def api_mapa_datos():
    filtro = (request.args.get('filtro') or 'todos').strip().lower()
    vecinos = Vecino.query.filter_by(activo=True).order_by(Vecino.domicilio.asc()).all()

    if filtro == 'con_ubicacion':
        vecinos = [v for v in vecinos if _tiene_ubicacion_mapa(v)]
    elif filtro == 'sin_ubicacion':
        vecinos = [v for v in vecinos if _vecino_necesita_geocodificacion(v) or not _tiene_ubicacion_mapa(v)]

    marcadores = _marcadores_mapa_desde_vecinos(vecinos)
    stats = _stats_mapa(Vecino.query.filter_by(activo=True).all())

    sin_ubicacion = [{
        'id': v.id,
        'nombre': v.nombre,
        'apellidos': v.apellidos,
        'domicilio': v.domicilio,
        'error': v.geocodificacion_error or '',
    } for v in Vecino.query.filter_by(activo=True).order_by(Vecino.domicilio.asc()).all()
        if _vecino_necesita_geocodificacion(v)]

    cfg = _map_config()
    return jsonify({
        **cfg,
        'stats': stats,
        'marcadores': marcadores,
        'sin_ubicacion': sin_ubicacion,
        'status': 'ok' if marcadores or not sin_ubicacion else 'sin_datos',
    })


@app.route('/api/mapa/referencias-picker')
@login_required
def api_mapa_referencias_picker():
    """Domicilios ya ubicados en el mapa, para mostrar al seleccionar pin manual."""
    excluir_id = request.args.get('excluir_id', type=int)
    vecinos = Vecino.query.filter_by(activo=True).all()
    if excluir_id:
        vecinos = [v for v in vecinos if v.id != excluir_id]
    marcadores = _marcadores_mapa_desde_vecinos(vecinos)
    return jsonify({
        'marcadores': [{
            'domicilio': m['domicilio'],
            'lat': m['lat'],
            'lng': m['lng'],
        } for m in marcadores if m.get('lat') is not None and m.get('lng') is not None],
    })


@app.route('/api/geocodificar-domicilio', methods=['POST'])
@login_required
def api_geocodificar_domicilio():
    data = request.get_json(silent=True) or {}
    domicilio = (data.get('domicilio') or request.form.get('domicilio') or '').strip()
    if not domicilio:
        return jsonify({'ok': False, 'error': 'Ingresa un domicilio.'}), 400
    lat, lon, err, exacta = _geocodificar_domicilio(domicilio)
    if lat is None or lon is None:
        return jsonify({
            'ok': False,
            'error': err or 'No se encontraron coordenadas para esa dirección.',
            'sugerencia': 'Prueba con calle y número (ej: Paul Harris 1234) o usa «Seleccionar en el Mapa».',
        }), 422
    return jsonify({
        'ok': True,
        'lat': lat,
        'lng': lon,
        'en_cuadrante': _coords_dentro_cuadrante(lat, lon),
        'aviso': err,
        'exacta': exacta,
    })


@app.route('/api/mapa/sincronizacion')
@login_required
def api_mapa_sincronizacion():
    return jsonify(_estado_sincronizacion_mapa())


@app.route('/api/mapa/geocodificar', methods=['POST'])
@login_required
def api_mapa_geocodificar():
    data = request.get_json(silent=True) or {}
    force = str(request.args.get('force') or data.get('force') or '').lower() in {'1', 'true', 'si', 'sí', 'yes'}
    iniciada = _iniciar_sincronizacion_ubicaciones(force=force)
    estado = _estado_sincronizacion_mapa()
    if iniciada:
        return jsonify({
            'status': 'started',
            'message': f"Sincronizando {estado['total']} vecino(s) en segundo plano...",
            **estado,
        })
    if estado['running']:
        return jsonify({
            'status': 'running',
            'message': 'La sincronización ya está en curso.',
            **estado,
        })
    return jsonify({
        'status': 'idle',
        'message': estado['last_message'] or 'No hay vecinos pendientes de ubicación.',
        **estado,
    })


@app.route('/exportar-excel')
@login_required
def exportar_excel():
    vecinos = Vecino.query.filter_by(activo=True).order_by(Vecino.nombre.asc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Vecinos'
    # Encabezados
    ws.append([
        '#', 'Nombre', 'Apellidos', 'RUT', 'Fecha Nacimiento', 'Edad',
        'Rango Etario', 'Domicilio', 'Teléfono', 'Fecha Registro', 'Notas',
    ])
    # Datos
    for idx, v in enumerate(vecinos, 1):
        ws.append([
            idx,
            v.nombre,
            v.apellidos,
            v.rut,
            v.fecha_nacimiento.strftime('%d/%m/%Y') if v.fecha_nacimiento else '',
            v.edad if v.edad is not None else '',
            v.rango_etario,
            v.domicilio,
            v.telefono or '',
            v.fecha_registro.strftime('%d/%m/%Y'),
            v.notas or ''
        ])
    # Guardar en memoria
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name='vecinos.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def _allowed_upload(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in {'.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx', '.xls', '.xlsx'}


def _allowed_document_upload(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in {'.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt'}


def _documento_permite_vista_previa(filename: str) -> bool:
    """Solo PDF e imágenes se muestran en el navegador; Word/Excel/PPT/TXT no."""
    ext = os.path.splitext((filename or '').strip())[1].lower()
    return ext in {'.pdf', '.png', '.jpg', '.jpeg'}

# Tipo reservado: los PDF se generan solo desde Certificados de residencia (no desde subida genérica).
DOCUMENTO_TIPO_CERTIFICADO_RESIDENCIA = 'Certificados de residencia'


def _normalize_doc_tipo(tipo_raw: str) -> str:
    tipo = (tipo_raw or '').strip()
    if not tipo:
        return 'Otros'
    # Evitar strings enormes
    return tipo[:50]


def _es_tipo_certificado_residencia(tipo_raw: str) -> bool:
    if not (tipo_raw or '').strip():
        return False
    return _normalize_doc_tipo(tipo_raw).lower() == DOCUMENTO_TIPO_CERTIFICADO_RESIDENCIA.lower()


def _tipos_para_subida_generica():
    rows = DocumentoTipo.query.filter_by(activo=True).order_by(DocumentoTipo.nombre.asc()).all()
    return [t for t in rows if not _es_tipo_certificado_residencia(t.nombre)]


@app.context_processor
def inject_doc_constants():
    return {
        'DOC_TIPO_CERT_RESIDENCIA': DOCUMENTO_TIPO_CERTIFICADO_RESIDENCIA,
        'documento_permite_vista_previa': _documento_permite_vista_previa,
    }


def _registros_movimiento_solo_coherentes(query):
    """
    Historial alineado con la BD: no muestra filas cuyo usuario ya no existe,
    ni acciones (excepto 'eliminar') sobre entidades que ya no existen.
    Así, si borras datos a mano en MySQL, el historial deja de mostrar movimientos huérfanos.
    """
    uids = db.session.query(Usuario.id)
    vids = db.session.query(Vecino.id)
    cids = db.session.query(CertificadoResidencia.id)
    dids = db.session.query(Documento.id)
    tids = db.session.query(DocumentoTipo.id)
    return query.filter(
        RegistroMovimiento.usuario_id.in_(uids),
        db.or_(
            RegistroMovimiento.accion == 'eliminar',
            db.and_(RegistroMovimiento.entidad == 'vecino', RegistroMovimiento.entidad_id.in_(vids)),
            db.and_(RegistroMovimiento.entidad == 'certificado', RegistroMovimiento.entidad_id.in_(cids)),
            db.and_(RegistroMovimiento.entidad == 'documento', RegistroMovimiento.entidad_id.in_(dids)),
            db.and_(RegistroMovimiento.entidad == 'tipo_documento', RegistroMovimiento.entidad_id.in_(tids)),
            db.and_(RegistroMovimiento.entidad == 'usuario', RegistroMovimiento.entidad_id.in_(uids)),
        ),
    )


def _registrar_movimiento(entidad: str, entidad_id: int, accion: str, detalles: str = None) -> None:
    try:
        mov = RegistroMovimiento(
            usuario_id=current_user.id,
            usuario_nombre=current_user.username,
            entidad=entidad,
            entidad_id=int(entidad_id),
            accion=accion,
            detalles=detalles
        )
        db.session.add(mov)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _safe_remove_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        # No interrumpir flujos por errores de filesystem
        pass


def _asegurar_tipo_documento(nombre: str) -> str:
    tipo = _normalize_doc_tipo(nombre)
    existente = DocumentoTipo.query.filter_by(nombre=tipo).first()
    if existente:
        if not existente.activo:
            existente.activo = True
            db.session.commit()
        return existente.nombre
    t = DocumentoTipo(nombre=tipo)
    db.session.add(t)
    db.session.commit()
    _registrar_movimiento(
        entidad='tipo_documento',
        entidad_id=t.id,
        accion='crear',
        detalles=f"Tipo de documento creado: {t.nombre}"
    )
    return t.nombre


def _generar_pdf_certificado(cert: "CertificadoResidencia") -> tuple[str, str]:
    """
    Genera el PDF desde el mismo HTML de la vista previa (para que se vea igual).
    Retorna (archivo_nombre, archivo_ruta).
    """
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_base = secure_filename(f"certificado_{cert.id}_{cert.nombres}_{cert.apellidos}_{cert.fecha.isoformat()}.pdf")
    if not safe_base.lower().endswith('.pdf'):
        safe_base = f"{safe_base}.pdf"
    stamped = f"certhtml_{ts}_{safe_base}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], stamped)

    logo_path = os.path.join(app.root_path, 'static', 'junta de vecinos.jpg')
    logo_src = None
    try:
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            # asumimos jpg por el nombre actual del archivo
            logo_src = f"data:image/jpeg;base64,{b64}"
    except Exception:
        logo_src = None

    html = render_template('certificado_plantilla.html', cert=cert, embed=True, logo_src=logo_src)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content(html, wait_until="load")
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
        )
        browser.close()

    with open(path, "wb") as f:
        f.write(pdf_bytes)

    return (os.path.basename(path), path)


@app.route('/certificados')
@login_required
def certificados():
    f_fecha = _arg('f_fecha')
    f_nombres = _arg('f_nombres')
    f_apellidos = _arg('f_apellidos')
    f_rut = _arg('f_rut')
    f_direccion = _arg('f_direccion')
    f_pago = _arg('f_pago').upper()
    sort_by = request.args.get('sort_by', 'fecha')
    sort_order = request.args.get('sort_order', 'desc')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = CertificadoResidencia.query.filter_by(activo=True)
    query = _apply_ilike(query, CertificadoResidencia.nombres, f_nombres)
    query = _apply_ilike(query, CertificadoResidencia.apellidos, f_apellidos)
    query = _apply_rut_ilike(query, CertificadoResidencia.rut, f_rut)
    query = _apply_ilike(query, CertificadoResidencia.direccion, f_direccion)

    if f_fecha:
        fecha_parsed = _parse_date_flexible(f_fecha)
        if fecha_parsed:
            query = query.filter(CertificadoResidencia.fecha == fecha_parsed)

    if f_pago in {'SI', 'NO'}:
        query = query.filter(CertificadoResidencia.pago == (f_pago == 'SI'))

    sort_col = {
        'fecha': CertificadoResidencia.fecha,
        'nombres': CertificadoResidencia.nombres,
        'apellidos': CertificadoResidencia.apellidos,
        'rut': CertificadoResidencia.rut,
        'direccion': CertificadoResidencia.direccion,
        'pago': CertificadoResidencia.pago,
        'fecha_creacion': CertificadoResidencia.fecha_creacion,
    }.get(sort_by, CertificadoResidencia.fecha)
    query = _order_by_col(query, sort_col, sort_order)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    certificados_items = pagination.items

    total_certificados = CertificadoResidencia.query.filter_by(activo=True).count()
    certificados_filtrados = query.count()
    has_filters = _has_any_filter(f_fecha, f_nombres, f_apellidos, f_rut, f_direccion, f_pago)
    page_params = {
        'f_fecha': f_fecha,
        'f_nombres': f_nombres,
        'f_apellidos': f_apellidos,
        'f_rut': f_rut,
        'f_direccion': f_direccion,
        'f_pago': f_pago,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }

    return render_template(
        'certificados.html',
        certificados=certificados_items,
        pagination=pagination,
        sort_by=sort_by,
        sort_order=sort_order,
        total_certificados=total_certificados,
        certificados_filtrados=certificados_filtrados,
        f_fecha=f_fecha,
        f_nombres=f_nombres,
        f_apellidos=f_apellidos,
        f_rut=f_rut,
        f_direccion=f_direccion,
        f_pago=f_pago,
        has_filters=has_filters,
        page_params=page_params,
    )


@app.route('/certificados/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_certificado():
    def _next_cert_redirect():
        n = (request.form.get('next') if request.method == 'POST' else request.args.get('next')) or ''
        n = (n or '').strip()
        return n if n.startswith('/') else ''

    next_url = _next_cert_redirect()

    if request.method == 'POST':
        fecha_str = request.form.get('fecha', '').strip()
        try:
            fecha = datetime.date.fromisoformat(fecha_str)
        except Exception:
            flash('Fecha inválida. Usa el formato YYYY-MM-DD.', 'error')
            return render_template(
                'nuevo_certificado.html', form_data=request.form, next_url=next_url
            )

        nombres = request.form.get('nombres', '').strip()
        apellidos = request.form.get('apellidos', '').strip()
        rut = request.form.get('rut', '').strip()
        direccion = request.form.get('direccion', '').strip()
        presentado_en = request.form.get('presentado_en', '').strip()
        pago_raw = request.form.get('pago', '').strip().lower()

        if not (nombres and apellidos and rut and direccion and presentado_en):
            flash('Completa todos los campos obligatorios.', 'error')
            return render_template(
                'nuevo_certificado.html', form_data=request.form, next_url=next_url
            )

        es_valido, mensaje_error = validar_rut(rut)
        if not es_valido:
            flash(mensaje_error, 'error')
            return render_template(
                'nuevo_certificado.html', form_data=request.form, next_url=next_url
            )

        pago = pago_raw in {'si', 'sí', 'true', '1', 'on'}

        cert = CertificadoResidencia(
            fecha=fecha,
            nombres=nombres,
            apellidos=apellidos,
            rut=formatear_rut(rut),
            direccion=direccion,
            presentado_en=presentado_en,
            pago=pago,
        )
        db.session.add(cert)
        db.session.commit()

        # Generar PDF y guardarlo como Documento en "Certificados de residencia"
        try:
            tipo_cert = _asegurar_tipo_documento(DOCUMENTO_TIPO_CERTIFICADO_RESIDENCIA)
            pdf_nombre, pdf_ruta = _generar_pdf_certificado(cert)
            doc = Documento(
                nombre=f"Certificado {cert.nombres} {cert.apellidos}",
                tipo=tipo_cert,
                archivo_nombre=pdf_nombre,
                archivo_ruta=pdf_ruta
            )
            db.session.add(doc)
            db.session.commit()
            cert.documento_id = doc.id
            db.session.commit()
            _registrar_movimiento(
                entidad='documento',
                entidad_id=doc.id,
                accion='crear',
                detalles=f"Documento generado desde certificado: {cert.nombres} {cert.apellidos} ({cert.rut})"
            )
        except Exception:
            db.session.rollback()

        _registrar_movimiento(
            entidad='certificado',
            entidad_id=cert.id,
            accion='crear',
            detalles=f"Certificado creado: {cert.nombres} {cert.apellidos} ({cert.rut}) - {cert.fecha.isoformat()} - pago: {'SI' if cert.pago else 'NO'}"
        )
        flash('Certificado agregado exitosamente', 'success')
        if next_url:
            return redirect(next_url)
        return redirect(url_for('certificados'))

    today = datetime.date.today().isoformat()
    return render_template(
        'nuevo_certificado.html', form_data={'fecha': today}, next_url=next_url
    )


@app.route('/certificados/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_certificado(id):
    cert = CertificadoResidencia.query.get_or_404(id)
    if not cert.activo:
        flash('Este certificado está desactivado.', 'error')
        return redirect(url_for('certificados'))

    if request.method == 'POST':
        fecha_str = request.form.get('fecha', '').strip()
        try:
            cert.fecha = datetime.date.fromisoformat(fecha_str)
        except Exception:
            flash('Fecha inválida. Usa el formato YYYY-MM-DD.', 'error')
            return render_template('editar_certificado.html', cert=cert, form_data=request.form)

        cert.nombres = request.form.get('nombres', '').strip()
        cert.apellidos = request.form.get('apellidos', '').strip()
        cert.rut = request.form.get('rut', '').strip()
        cert.direccion = request.form.get('direccion', '').strip()
        cert.presentado_en = request.form.get('presentado_en', '').strip()
        pago_raw = request.form.get('pago', '').strip().lower()
        cert.pago = pago_raw in {'si', 'sí', 'true', '1', 'on'}

        if not (cert.nombres and cert.apellidos and cert.rut and cert.direccion and cert.presentado_en):
            flash('Completa todos los campos obligatorios.', 'error')
            return render_template('editar_certificado.html', cert=cert, form_data=request.form)

        es_valido, mensaje_error = validar_rut(cert.rut)
        if not es_valido:
            flash(mensaje_error, 'error')
            return render_template('editar_certificado.html', cert=cert, form_data=request.form)
        cert.rut = formatear_rut(cert.rut)

        # Regenerar PDF si existe Documento vinculado, si no, crearlo
        try:
            tipo_cert = _asegurar_tipo_documento(DOCUMENTO_TIPO_CERTIFICADO_RESIDENCIA)
            pdf_nombre, pdf_ruta = _generar_pdf_certificado(cert)
            doc = None
            if cert.documento_id:
                doc = Documento.query.get(cert.documento_id)
                if doc and doc.activo:
                    doc.tipo = tipo_cert
                    doc.nombre = f"Certificado {cert.nombres} {cert.apellidos}"
                    doc.archivo_nombre = pdf_nombre
                    doc.archivo_ruta = pdf_ruta
                    db.session.commit()
                else:
                    doc = None
            if not doc:
                doc = Documento(
                    nombre=f"Certificado {cert.nombres} {cert.apellidos}",
                    tipo=tipo_cert,
                    archivo_nombre=pdf_nombre,
                    archivo_ruta=pdf_ruta
                )
                db.session.add(doc)
                db.session.commit()
                cert.documento_id = doc.id
                db.session.commit()
                _registrar_movimiento(
                    entidad='documento',
                    entidad_id=doc.id,
                    accion='crear',
                    detalles=f"Documento generado desde certificado (vinculación): {cert.nombres} {cert.apellidos} ({cert.rut})"
                )
            else:
                _registrar_movimiento(
                    entidad='documento',
                    entidad_id=doc.id,
                    accion='editar',
                    detalles=f"Documento (PDF) regenerado desde certificado: {cert.nombres} {cert.apellidos} ({cert.rut})"
                )
        except Exception:
            db.session.rollback()

        db.session.commit()
        _registrar_movimiento(
            entidad='certificado',
            entidad_id=cert.id,
            accion='editar',
            detalles=f"Certificado actualizado: {cert.nombres} {cert.apellidos} ({cert.rut}) - {cert.fecha.isoformat()} - pago: {'SI' if cert.pago else 'NO'}"
        )
        flash('Certificado actualizado exitosamente', 'success')
        return redirect(url_for('certificados'))

    return render_template('editar_certificado.html', cert=cert, form_data=None)


@app.route('/certificados/<int:id>/eliminar')
@login_required
def eliminar_certificado(id):
    cert = CertificadoResidencia.query.get_or_404(id)
    # Eliminar definitivamente (hard delete)
    doc = None
    if cert.documento_id:
        doc = Documento.query.get(cert.documento_id)

    _registrar_movimiento(
        entidad='certificado',
        entidad_id=cert.id,
        accion='eliminar',
        detalles=f"Certificado eliminado definitivamente: {cert.nombres} {cert.apellidos} ({cert.rut}) - {cert.fecha.isoformat()}"
    )

    if doc:
        _registrar_movimiento(
            entidad='documento',
            entidad_id=doc.id,
            accion='eliminar',
            detalles=f"Documento eliminado por eliminar certificado: {doc.nombre} (tipo: {doc.tipo}) - {doc.archivo_nombre}"
        )

    # Borrar archivo del documento si existe
    if doc and doc.archivo_ruta:
        _safe_remove_file(doc.archivo_ruta)

    try:
        if doc:
            db.session.delete(doc)
        db.session.delete(cert)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('No se pudo eliminar el certificado.', 'error')
        return redirect(url_for('certificados'))

    flash('Certificado eliminado definitivamente', 'success')
    return redirect(url_for('certificados'))


@app.route('/certificados/<int:id>/imprimir')
@login_required
def imprimir_certificado(id):
    cert = CertificadoResidencia.query.get_or_404(id)
    if not cert.activo:
        flash('Este certificado está desactivado.', 'error')
        return redirect(url_for('certificados'))

    _registrar_movimiento(
        entidad='certificado',
        entidad_id=cert.id,
        accion='ver',
        detalles=f"Abrió plantilla de certificado para imprimir: {cert.nombres} {cert.apellidos} ({cert.rut})"
    )

    embed = (request.args.get('embed') or '').strip().lower() in {'1', 'true', 'si', 'sí', 'yes', 'y'}
    return render_template('certificado_plantilla.html', cert=cert, embed=embed)


@app.route('/certificados/<int:id>/ver')
@login_required
def ver_certificado(id):
    """Vista previa embebible: PDF del certificado si existe, si no la plantilla HTML."""
    cert = CertificadoResidencia.query.get_or_404(id)
    if not cert.activo:
        flash('Este certificado está desactivado.', 'error')
        return redirect(url_for('certificados'))

    if cert.documento_id:
        doc = Documento.query.get(cert.documento_id)
        if doc and doc.archivo_ruta and os.path.exists(doc.archivo_ruta):
            nombre_arch = doc.archivo_nombre or os.path.basename(doc.archivo_ruta)
            if _documento_permite_vista_previa(nombre_arch):
                _registrar_movimiento(
                    entidad='certificado',
                    entidad_id=cert.id,
                    accion='ver',
                    detalles=f"Vista previa de certificado: {cert.nombres} {cert.apellidos} ({cert.rut})"
                )
                return send_file(
                    doc.archivo_ruta,
                    as_attachment=False,
                    download_name=nombre_arch,
                )

    _registrar_movimiento(
        entidad='certificado',
        entidad_id=cert.id,
        accion='ver',
        detalles=f"Vista previa de certificado (plantilla): {cert.nombres} {cert.apellidos} ({cert.rut})"
    )
    return render_template('certificado_plantilla.html', cert=cert, embed=True)


@app.route('/certificados/<int:id>/pdf')
@login_required
def descargar_pdf_certificado(id):
    cert = CertificadoResidencia.query.get_or_404(id)
    doc = None
    if cert.documento_id:
        doc = Documento.query.get(cert.documento_id)
    if not doc or not doc.archivo_ruta or not os.path.exists(doc.archivo_ruta):
        flash('No se encuentra el PDF del certificado.', 'error')
        return redirect(url_for('certificados'))

    _registrar_movimiento(
        entidad='certificado',
        entidad_id=cert.id,
        accion='descargar',
        detalles=f"Descargó PDF de certificado: {cert.nombres} {cert.apellidos} ({cert.rut})"
    )
    _registrar_movimiento(
        entidad='documento',
        entidad_id=doc.id,
        accion='descargar',
        detalles=f"Descargó documento (PDF) desde certificado: {doc.nombre} - {doc.archivo_nombre}"
    )

    return send_file(
        doc.archivo_ruta,
        as_attachment=True,
        download_name=doc.archivo_nombre or os.path.basename(doc.archivo_ruta)
    )


@app.route('/documentos')
@login_required
def documentos():
    # Cards por tipo (mostrar tipos aunque tengan 0 documentos)
    tipos = DocumentoTipo.query.filter_by(activo=True).order_by(DocumentoTipo.nombre.asc()).all()
    counts = dict(
        db.session.query(Documento.tipo, db.func.count(Documento.id))
        .filter(Documento.activo == True)  # noqa: E712
        .group_by(Documento.tipo)
        .all()
    )
    cards = [
        {'tipo': t.nombre, 'tipo_id': t.id, 'cantidad': int(counts.get(t.nombre, 0))}
        for t in tipos
    ]
    total = int(Documento.query.filter_by(activo=True).count())
    return render_template('documentos.html', cards=cards, total=total, es_admin=_es_admin(current_user))

@app.route('/documentos/tipos', methods=['GET', 'POST'])
@login_required
def documentos_tipos():
    if request.method == 'POST':
        nombre = _normalize_doc_tipo(request.form.get('nombre', ''))
        if not nombre:
            flash('El nombre del tipo es obligatorio.', 'error')
            return redirect(url_for('documentos_tipos'))

        existente = DocumentoTipo.query.filter_by(nombre=nombre).first()
        if existente:
            if not existente.activo:
                existente.activo = True
                db.session.commit()
                flash('Tipo reactivado exitosamente.', 'success')
            else:
                flash('Ese tipo ya existe.', 'info')
            return redirect(url_for('documentos_tipos'))

        t = DocumentoTipo(nombre=nombre)
        db.session.add(t)
        db.session.commit()
        _registrar_movimiento(
            entidad='tipo_documento',
            entidad_id=t.id,
            accion='crear',
            detalles=f"Tipo de documento creado: {t.nombre}"
        )
        flash('Tipo agregado exitosamente.', 'success')
        return redirect(url_for('documentos_tipos'))

    tipos = DocumentoTipo.query.filter_by(activo=True).order_by(DocumentoTipo.nombre.asc()).all()
    return render_template('documentos_tipos.html', tipos=tipos)


@app.route('/documentos/tipos/<int:id>/eliminar')
@login_required
def eliminar_tipo_documento(id):
    if not _es_admin(current_user):
        flash('Solo un administrador puede eliminar tipos de documento.', 'error')
        return redirect(url_for('documentos'))

    t = DocumentoTipo.query.get_or_404(id)
    if not t.activo:
        flash('Este tipo ya estaba desactivado.', 'info')
        return redirect(url_for('documentos'))

    if _es_tipo_certificado_residencia(t.nombre):
        flash(
            'No se puede eliminar el tipo reservado «Certificados de residencia» (está vinculado a certificados).',
            'error',
        )
        return redirect(url_for('documentos'))

    n_docs = Documento.query.filter_by(activo=True, tipo=t.nombre).count()
    if n_docs > 0:
        flash(
            f'No se puede eliminar el tipo «{t.nombre}»: hay {n_docs} documento(s) asociado(s). '
            'Elimina o reasigna esos documentos primero.',
            'error',
        )
        return redirect(url_for('documentos'))

    _registrar_movimiento(
        entidad='tipo_documento',
        entidad_id=t.id,
        accion='eliminar',
        detalles=f'Tipo de documento eliminado (desactivado): {t.nombre}',
    )
    t.activo = False
    db.session.commit()
    flash(f'Tipo «{t.nombre}» eliminado. Ya no aparecerá en la lista de documentos.', 'success')
    return redirect(url_for('documentos'))


@app.route('/documentos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_documento():
    if request.method == 'GET':
        qtipo = request.args.get('tipo', '').strip()
        if qtipo and _es_tipo_certificado_residencia(qtipo):
            next_u = (request.args.get('next') or '').strip()
            if next_u.startswith('/'):
                return redirect(url_for('nuevo_certificado', next=next_u))
            return redirect(url_for('nuevo_certificado'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = _normalize_doc_tipo(request.form.get('tipo', ''))
        archivo = request.files.get('archivo')

        if _es_tipo_certificado_residencia(tipo):
            flash(
                'Los certificados de residencia se crean desde la sección Certificados, con el formulario completo (RUT, dirección, etc.).',
                'error',
            )
            return redirect(url_for('nuevo_certificado'))

        if not nombre:
            flash('El nombre es obligatorio.', 'error')
            tipos = _tipos_para_subida_generica()
            return render_template('nuevo_documento.html', form_data=request.form, tipos=tipos)
        # Solo permitir tipos existentes (para evitar duplicados)
        if not DocumentoTipo.query.filter_by(activo=True, nombre=tipo).first():
            flash('Selecciona un tipo válido (creado previamente).', 'error')
            tipos = _tipos_para_subida_generica()
            return render_template('nuevo_documento.html', form_data=request.form, tipos=tipos)
        if not archivo or not archivo.filename:
            flash('Debes adjuntar un archivo.', 'error')
            tipos = _tipos_para_subida_generica()
            return render_template('nuevo_documento.html', form_data=request.form, tipos=tipos)
        if not _allowed_document_upload(archivo.filename):
            flash('Tipo de archivo no permitido. Usa PDF/Imagen/Word/Excel/PPT/TXT.', 'error')
            tipos = _tipos_para_subida_generica()
            return render_template('nuevo_documento.html', form_data=request.form, tipos=tipos)

        safe_name = secure_filename(archivo.filename)
        stamped = f"doc_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], stamped)
        archivo.save(path)

        doc = Documento(
            nombre=nombre,
            tipo=tipo,
            archivo_nombre=safe_name,
            archivo_ruta=path
        )
        db.session.add(doc)
        db.session.commit()
        _registrar_movimiento(
            entidad='documento',
            entidad_id=doc.id,
            accion='crear',
            detalles=f"Documento subido: {doc.nombre} (tipo: {doc.tipo}) - {doc.archivo_nombre}"
        )
        flash('Documento subido exitosamente', 'success')
        return redirect(url_for('documentos'))

    tipos = _tipos_para_subida_generica()
    form_data = None
    pre = request.args.get('tipo', '').strip()
    if pre and not _es_tipo_certificado_residencia(pre):
        form_data = {'nombre': '', 'tipo': _normalize_doc_tipo(pre)}
    return render_template('nuevo_documento.html', form_data=form_data, tipos=tipos)


@app.route('/documentos/tipo/<string:tipo>')
@login_required
def documentos_por_tipo(tipo):
    tipo_norm = _normalize_doc_tipo(tipo)
    f_nombre = _arg('f_nombre')
    f_archivo = _arg('f_archivo')
    f_fecha = _arg('f_fecha')
    sort_by = request.args.get('sort_by', 'fecha_creacion')
    sort_order = request.args.get('sort_order', 'desc')
    page = request.args.get('page', 1, type=int)
    per_page = 12

    query = Documento.query.filter_by(activo=True, tipo=tipo_norm)
    query = _apply_ilike(query, Documento.nombre, f_nombre)
    query = _apply_ilike(query, Documento.archivo_nombre, f_archivo)

    if f_fecha:
        fecha_parsed = _parse_date_flexible(f_fecha)
        if fecha_parsed:
            query = query.filter(db.func.date(Documento.fecha_creacion) == fecha_parsed)

    sort_col = {
        'nombre': Documento.nombre,
        'archivo': Documento.archivo_nombre,
        'fecha_creacion': Documento.fecha_creacion,
    }.get(sort_by, Documento.fecha_creacion)
    query = _order_by_col(query, sort_col, sort_order)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    has_filters = _has_any_filter(f_nombre, f_archivo, f_fecha)
    page_params = {
        'f_nombre': f_nombre,
        'f_archivo': f_archivo,
        'f_fecha': f_fecha,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }
    return render_template(
        'documentos_tipo.html',
        tipo=tipo_norm,
        sort_by=sort_by,
        sort_order=sort_order,
        documentos=pagination.items,
        pagination=pagination,
        f_nombre=f_nombre,
        f_archivo=f_archivo,
        f_fecha=f_fecha,
        has_filters=has_filters,
        page_params=page_params,
    )


@app.route('/documentos/<int:id>/archivo')
@login_required
def descargar_archivo_documento(id):
    doc = Documento.query.get_or_404(id)
    if not doc.activo:
        flash('Este documento está desactivado.', 'error')
        return redirect(url_for('documentos'))
    if not doc.archivo_ruta or not os.path.exists(doc.archivo_ruta):
        flash('No se encuentra el archivo adjunto.', 'error')
        return redirect(url_for('documentos_por_tipo', tipo=doc.tipo))

    _registrar_movimiento(
        entidad='documento',
        entidad_id=doc.id,
        accion='descargar',
        detalles=f"Descargó documento: {doc.nombre} - {doc.archivo_nombre}"
    )
    return send_file(
        doc.archivo_ruta,
        as_attachment=True,
        download_name=doc.archivo_nombre or os.path.basename(doc.archivo_ruta)
    )

@app.route('/documentos/<int:id>/ver')
@login_required
def ver_documento(id):
    doc = Documento.query.get_or_404(id)
    if not doc.activo:
        flash('Este documento está desactivado.', 'error')
        return redirect(url_for('documentos'))
    if not doc.archivo_ruta or not os.path.exists(doc.archivo_ruta):
        flash('No se encuentra el archivo adjunto.', 'error')
        return redirect(url_for('documentos_por_tipo', tipo=doc.tipo))

    nombre_arch = doc.archivo_nombre or os.path.basename(doc.archivo_ruta)
    if not _documento_permite_vista_previa(nombre_arch):
        flash('La vista previa solo está disponible para archivos PDF e imágenes. Descarga el archivo para abrirlo.', 'info')
        return redirect(url_for('documentos_por_tipo', tipo=doc.tipo))

    _registrar_movimiento(
        entidad='documento',
        entidad_id=doc.id,
        accion='ver',
        detalles=f"Visualizó documento: {doc.nombre} - {doc.archivo_nombre}"
    )

    # El navegador abrirá inline cuando pueda (PDF/imagenes/texto, etc.)
    return send_file(
        doc.archivo_ruta,
        as_attachment=False,
        download_name=doc.archivo_nombre or os.path.basename(doc.archivo_ruta)
    )


@app.route('/documentos/<int:id>/eliminar')
@login_required
def eliminar_documento(id):
    doc = Documento.query.get_or_404(id)
    next_url = (request.args.get('next') or '').strip()
    if not next_url.startswith('/'):
        next_url = ''
    _registrar_movimiento(
        entidad='documento',
        entidad_id=doc.id,
        accion='eliminar',
        detalles=f"Documento eliminado definitivamente: {doc.nombre} (tipo: {doc.tipo}) - {doc.archivo_nombre}"
    )
    _safe_remove_file(doc.archivo_ruta)
    try:
        db.session.delete(doc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('No se pudo eliminar el documento.', 'error')
        return redirect(next_url or url_for('documentos_por_tipo', tipo=doc.tipo))

    flash('Documento eliminado definitivamente', 'success')
    return redirect(next_url or url_for('documentos_por_tipo', tipo=doc.tipo))

@app.route('/vecinos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_vecino():
    if request.method == 'POST':
        rut = request.form['rut'].strip()
        es_valido, mensaje_error = validar_rut(rut)
        form_data = _form_vecino_desde_request()
        form_data['rut'] = rut
        if not es_valido:
            flash(f'Error en RUT: {mensaje_error}', 'error')
            return render_template('nuevo_vecino.html', form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        existe, vecino_existente = rut_existe(rut)
        if existe:
            flash(f'El RUT ya está registrado por {vecino_existente.nombre} {vecino_existente.apellidos}', 'error')
            return render_template('nuevo_vecino.html', form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        fecha_nacimiento, err_fecha = _parse_fecha_nacimiento(form_data['fecha_nacimiento'])
        if err_fecha:
            flash(err_fecha, 'error')
            return render_template('nuevo_vecino.html', form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        rut_formateado = formatear_rut(rut)
        vecino = Vecino(
            nombre=form_data['nombre'],
            apellidos=form_data['apellidos'],
            telefono=form_data['telefono'],
            domicilio=form_data['domicilio'],
            rut=rut_formateado,
            fecha_nacimiento=fecha_nacimiento,
            notas=form_data['notas']
        )
        db.session.add(vecino)
        _aplicar_ubicacion_vecino(vecino, form_data['latitud'], form_data['longitud'])
        db.session.commit()
        # Registrar acción de creación
        registro = RegistroAccion(
            usuario_id=current_user.id,
            usuario_nombre=current_user.username,
            vecino_id=vecino.id,
            accion='crear',
            detalles=f"Vecino creado: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
        )
        db.session.add(registro)
        db.session.commit()
        _registrar_movimiento(
            entidad='vecino',
            entidad_id=vecino.id,
            accion='crear',
            detalles=f"Vecino creado: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
        )
        flash('Vecino agregado exitosamente', 'success')
        return redirect(url_for('dashboard'))
    return render_template('nuevo_vecino.html', form_data=None, map_config=_map_config(), today=datetime.date.today().isoformat())

@app.route('/vecinos/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_vecino(id):
    vecino = Vecino.query.get_or_404(id)
    if not vecino.activo:
        flash('Este vecino está desactivado.', 'error')
        return redirect(url_for('dashboard'))

    # Registrar acceso (GET) al formulario de edición
    if request.method == 'GET':
        registro = RegistroAccion(
            usuario_id=current_user.id,
            usuario_nombre=current_user.username,
            vecino_id=vecino.id,
            accion='ver',
            detalles=f"Accedió a edición de vecino: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
        )
        db.session.add(registro)
        db.session.commit()
        _registrar_movimiento(
            entidad='vecino',
            entidad_id=vecino.id,
            accion='ver',
            detalles=f"Accedió a edición de vecino: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
        )

    if request.method == 'POST':
        rut = request.form['rut'].strip()
        es_valido, mensaje_error = validar_rut(rut)
        form_data = _form_vecino_desde_request()
        form_data['rut'] = rut
        if not es_valido:
            flash(f'Error en RUT: {mensaje_error}', 'error')
            return render_template('editar_vecino.html', vecino=vecino, form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        existe, vecino_existente = rut_existe(rut, vecino.id)
        if existe:
            flash(f'El RUT ya está registrado por {vecino_existente.nombre} {vecino_existente.apellidos}', 'error')
            return render_template('editar_vecino.html', vecino=vecino, form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        fecha_nacimiento, err_fecha = _parse_fecha_nacimiento(form_data['fecha_nacimiento'])
        if err_fecha:
            flash(err_fecha, 'error')
            return render_template('editar_vecino.html', vecino=vecino, form_data=form_data, map_config=_map_config(), today=datetime.date.today().isoformat())
        rut_formateado = formatear_rut(rut)
        cambios = []
        if vecino.nombre != form_data['nombre']:
            cambios.append(f"Nombre: '{vecino.nombre}' → '{form_data['nombre']}'")
        if vecino.apellidos != form_data['apellidos']:
            cambios.append(f"Apellidos: '{vecino.apellidos}' → '{form_data['apellidos']}'")
        if vecino.telefono != form_data['telefono']:
            cambios.append(f"Teléfono: '{vecino.telefono}' → '{form_data['telefono']}'")
        if vecino.domicilio != form_data['domicilio']:
            cambios.append(f"Domicilio: '{vecino.domicilio}' → '{form_data['domicilio']}'")
        if vecino.rut != rut_formateado:
            cambios.append(f"RUT: '{vecino.rut}' → '{rut_formateado}'")
        if vecino.fecha_nacimiento != fecha_nacimiento:
            old_fn = vecino.fecha_nacimiento.isoformat() if vecino.fecha_nacimiento else '—'
            new_fn = fecha_nacimiento.isoformat() if fecha_nacimiento else '—'
            cambios.append(f"Fecha nacimiento: '{old_fn}' → '{new_fn}'")
        if vecino.notas != form_data['notas']:
            cambios.append(f"Notas: '{vecino.notas}' → '{form_data['notas']}'")
        domicilio_cambio = vecino.domicilio != form_data['domicilio']
        old_lat = vecino.latitud
        old_lng = vecino.longitud
        lat_new = _parse_coord(form_data['latitud'])
        lng_new = _parse_coord(form_data['longitud'])
        coords_cambiaron = (
            lat_new is not None and lng_new is not None
            and (
                old_lat is None or old_lng is None
                or abs(lat_new - old_lat) > 1e-6
                or abs(lng_new - old_lng) > 1e-6
            )
        )
        vecino.nombre = form_data['nombre']
        vecino.apellidos = form_data['apellidos']
        vecino.telefono = form_data['telefono']
        vecino.domicilio = form_data['domicilio']
        vecino.rut = rut_formateado
        vecino.fecha_nacimiento = fecha_nacimiento
        vecino.notas = form_data['notas']
        if coords_cambiaron:
            _aplicar_ubicacion_vecino(
                vecino, form_data['latitud'], form_data['longitud'], geocodificar_si_falta=False
            )
        elif domicilio_cambio or _vecino_necesita_geocodificacion(vecino):
            _geocodificar_vecino(vecino)
        db.session.commit()
        # Registrar acción de edición
        registro = RegistroAccion(
            usuario_id=current_user.id,
            usuario_nombre=current_user.username,
            vecino_id=vecino.id,
            accion='editar',
            detalles='; '.join(cambios) if cambios else 'Sin cambios relevantes'
        )
        db.session.add(registro)
        db.session.commit()
        _registrar_movimiento(
            entidad='vecino',
            entidad_id=vecino.id,
            accion='editar',
            detalles='; '.join(cambios) if cambios else 'Sin cambios relevantes'
        )
        flash('Vecino actualizado exitosamente', 'success')
        return redirect(url_for('dashboard'))
    return render_template('editar_vecino.html', vecino=vecino, form_data=None, map_config=_map_config(), today=datetime.date.today().isoformat())

@app.route('/vecinos/<int:id>/eliminar')
@login_required
def eliminar_vecino(id):
    vecino = Vecino.query.get_or_404(id)
    _registrar_movimiento(
        entidad='vecino',
        entidad_id=vecino.id,
        accion='eliminar',
        detalles=f"Vecino eliminado definitivamente: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
    )
    try:
        # Mantener también RegistroAccion (histórico viejo) por compatibilidad
        db.session.query(RegistroAccion).filter_by(vecino_id=vecino.id).delete(synchronize_session=False)
        db.session.delete(vecino)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('No se pudo eliminar el vecino.', 'error')
        return redirect(url_for('dashboard'))

    flash('Vecino eliminado definitivamente', 'success')
    return redirect(url_for('dashboard'))


@app.route('/vecinos/<int:id>')
@login_required
def ver_vecino(id):
    vecino = Vecino.query.get_or_404(id)
    if not vecino.activo:
        flash('Este vecino está desactivado.', 'error')
        return redirect(url_for('dashboard'))

    registro = RegistroAccion(
        usuario_id=current_user.id,
        usuario_nombre=current_user.username,
        vecino_id=vecino.id,
        accion='ver',
        detalles=f"Vio ficha de vecino: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
    )
    db.session.add(registro)
    db.session.commit()
    _registrar_movimiento(
        entidad='vecino',
        entidad_id=vecino.id,
        accion='ver',
        detalles=f"Vio ficha de vecino: {vecino.nombre} {vecino.apellidos} ({vecino.rut})"
    )

    return render_template('ver_vecino.html', vecino=vecino)


@app.route('/registros')
@login_required
def registros():
    if not _puede_ver_historial(current_user):
        flash('No tienes permisos para ver el historial.', 'error')
        return redirect(url_for('dashboard'))
    page = request.args.get('page', 1, type=int)
    per_page = 20
    sort_by = request.args.get('sort_by', 'fecha_hora')
    sort_order = request.args.get('sort_order', 'desc')

    f_usuario = _arg('f_usuario')
    f_desde = _arg('f_desde')
    f_hasta = _arg('f_hasta')
    f_accion = _arg('f_accion')
    f_entidad = _arg('f_entidad')
    f_detalles = _arg('f_detalles')

    query = RegistroMovimiento.query

    if f_usuario:
        term = f'%{f_usuario}%'
        if f_usuario.isdigit():
            query = query.filter(
                db.or_(
                    RegistroMovimiento.usuario_id == int(f_usuario),
                    RegistroMovimiento.usuario_nombre.ilike(term),
                )
            )
        else:
            query = query.filter(RegistroMovimiento.usuario_nombre.ilike(term))

    try:
        if f_desde:
            dt_desde = datetime.datetime.strptime(f_desde, '%Y-%m-%d')
            query = query.filter(RegistroMovimiento.fecha_hora >= dt_desde)
        if f_hasta:
            dt_hasta = datetime.datetime.strptime(f_hasta, '%Y-%m-%d')
            dt_hasta = dt_hasta.replace(hour=23, minute=59, second=59, microsecond=999999)
            query = query.filter(RegistroMovimiento.fecha_hora <= dt_hasta)
    except ValueError:
        flash('Formato de fecha inválido. Usa YYYY-MM-DD.', 'error')

    if f_accion:
        query = query.filter(RegistroMovimiento.accion == f_accion)
    query = _apply_ilike(query, RegistroMovimiento.entidad, f_entidad)
    query = _apply_ilike(query, RegistroMovimiento.detalles, f_detalles)

    query = _registros_movimiento_solo_coherentes(query)

    sort_col = {
        'fecha_hora': RegistroMovimiento.fecha_hora,
        'usuario': RegistroMovimiento.usuario_nombre,
        'accion': RegistroMovimiento.accion,
        'entidad': RegistroMovimiento.entidad,
    }.get(sort_by, RegistroMovimiento.fecha_hora)
    query = _order_by_col(query, sort_col, sort_order)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    has_filters = _has_any_filter(f_usuario, f_desde, f_hasta, f_accion, f_entidad, f_detalles)
    page_params = {
        'f_usuario': f_usuario,
        'f_desde': f_desde,
        'f_hasta': f_hasta,
        'f_accion': f_accion,
        'f_entidad': f_entidad,
        'f_detalles': f_detalles,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }
    return render_template(
        'registros.html',
        pagination=pagination,
        registros=pagination.items,
        sort_by=sort_by,
        sort_order=sort_order,
        f_usuario=f_usuario,
        f_desde=f_desde,
        f_hasta=f_hasta,
        f_accion=f_accion,
        f_entidad=f_entidad,
        f_detalles=f_detalles,
        has_filters=has_filters,
        page_params=page_params,
    )

@app.route('/validar-rut', methods=['GET', 'POST'])
def validar_rut_test():
    """Página para probar la validación de RUT"""
    resultado = None
    if request.method == 'POST':
        rut = request.form.get('rut', '').strip()
        if rut:
            es_valido, mensaje = validar_rut(rut)
            if es_valido:
                rut_formateado = formatear_rut(rut)
                resultado = {
                    'valido': True,
                    'mensaje': f'RUT válido: {rut_formateado}',
                    'rut_formateado': rut_formateado
                }
            else:
                resultado = {
                    'valido': False,
                    'mensaje': mensaje
                }
    
    return render_template('validar_rut.html', resultado=resultado)

@app.route('/api/verificar-rut', methods=['POST'])
def verificar_rut_api():
    """API para verificar si un RUT es válido y único"""
    rut = request.json.get('rut', '').strip()
    excluir_id = request.json.get('excluir_id')
    
    if not rut:
        return {'valido': False, 'mensaje': 'RUT no proporcionado'}
    
    # Validar formato del RUT
    es_valido, mensaje_error = validar_rut(rut)
    if not es_valido:
        return {'valido': False, 'mensaje': mensaje_error}
    
    # Verificar si ya existe
    existe, vecino_existente = rut_existe(rut, excluir_id)
    if existe:
        return {
            'valido': False, 
            'mensaje': f'El RUT ya está registrado por {vecino_existente.nombre} {vecino_existente.apellidos}'
        }
    
    return {'valido': True, 'mensaje': 'RUT válido y disponible'}


_db_schema_ready = False


def _ensure_db_schema():
    global _db_schema_ready
    if _db_schema_ready:
        return
    db.create_all()
    # Migración simple: asegurar columna Usuario.role exista ANTES de consultas ORM
    try:
        inspector = db.inspect(db.engine)
        if inspector.has_table('usuario'):
            cols = {c['name'] for c in inspector.get_columns('usuario')}
            if 'role' not in cols:
                db.session.execute(db.text("ALTER TABLE usuario ADD COLUMN role VARCHAR(30) NOT NULL DEFAULT 'Asistente'"))
                db.session.commit()
    except Exception:
        db.session.rollback()

    # Migración simple: asegurar columnas de Vecino (activo, mapa, etc.)
    try:
        inspector = db.inspect(db.engine)
        if inspector.has_table('vecino'):
            cols = {c['name'] for c in inspector.get_columns('vecino')}
            if 'activo' not in cols:
                db.session.execute(db.text("ALTER TABLE vecino ADD COLUMN activo TINYINT(1) NOT NULL DEFAULT 1"))
                db.session.commit()
            cols = {c['name'] for c in inspector.get_columns('vecino')}
            map_cols = {
                'latitud': "ALTER TABLE vecino ADD COLUMN latitud DOUBLE NULL",
                'longitud': "ALTER TABLE vecino ADD COLUMN longitud DOUBLE NULL",
                'geocodificado_en': "ALTER TABLE vecino ADD COLUMN geocodificado_en DATETIME NULL",
                'geocodificacion_error': "ALTER TABLE vecino ADD COLUMN geocodificacion_error VARCHAR(255) NULL",
                'domicilio_mapeado': "ALTER TABLE vecino ADD COLUMN domicilio_mapeado VARCHAR(200) NULL",
            }
            for col_name, ddl in map_cols.items():
                if col_name not in cols:
                    db.session.execute(db.text(ddl))
                    db.session.commit()
                    cols.add(col_name)
            if 'fecha_nacimiento' not in cols:
                db.session.execute(db.text("ALTER TABLE vecino ADD COLUMN fecha_nacimiento DATE NULL"))
                db.session.commit()
            db.session.execute(db.text(
                "UPDATE vecino SET domicilio_mapeado = domicilio "
                "WHERE activo = 1 AND latitud IS NOT NULL AND longitud IS NOT NULL "
                "AND (domicilio_mapeado IS NULL OR domicilio_mapeado = '')"
            ))
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Migración simple: asegurar columna CertificadoResidencia.pago exista
    try:
        inspector = db.inspect(db.engine)
        if inspector.has_table('certificado_residencia'):
            cols = {c['name'] for c in inspector.get_columns('certificado_residencia')}
            if 'pago' not in cols:
                db.session.execute(db.text("ALTER TABLE certificado_residencia ADD COLUMN pago TINYINT(1) NOT NULL DEFAULT 0"))
                db.session.commit()
    except Exception:
        db.session.rollback()

    # Migración simple: asegurar columna CertificadoResidencia.presentado_en exista
    try:
        inspector = db.inspect(db.engine)
        if inspector.has_table('certificado_residencia'):
            cols = {c['name'] for c in inspector.get_columns('certificado_residencia')}
            if 'presentado_en' not in cols:
                db.session.execute(db.text("ALTER TABLE certificado_residencia ADD COLUMN presentado_en VARCHAR(200) NULL"))
                db.session.commit()
    except Exception:
        db.session.rollback()

    # Migración simple: asegurar columna CertificadoResidencia.documento_id exista
    try:
        inspector = db.inspect(db.engine)
        if inspector.has_table('certificado_residencia'):
            cols = {c['name'] for c in inspector.get_columns('certificado_residencia')}
            if 'documento_id' not in cols:
                db.session.execute(db.text("ALTER TABLE certificado_residencia ADD COLUMN documento_id INT NULL"))
                db.session.commit()
    except Exception:
        db.session.rollback()

    # Crear usuario admin por defecto si no existe
    if not Usuario.query.filter_by(username='admin').first():
        admin = Usuario(username='admin', email='admin@junta.com', es_admin=True, role='Admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

    # Backfill: si es_admin -> Admin
    try:
        db.session.execute(db.text("UPDATE usuario SET role='Admin' WHERE (es_admin=1 OR es_admin=true) AND (role IS NULL OR role='')"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    _db_schema_ready = True


@app.before_request
def _init_db_schema_once():
    _ensure_db_schema()


if __name__ == '__main__':
    with app.app_context():
        _ensure_db_schema()
    
    # host='0.0.0.0' permite acceso desde la LAN (ej. http://192.168.1.93:5000)
    app.run(debug=True, host='0.0.0.0', port=5000)