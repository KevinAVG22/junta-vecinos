from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import re
import datetime
from pathlib import Path
import base64
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import io
import openpyxl

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'tu-clave-secreta-aqui')
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


#ruta default
@app.route("/")
def home():
    return "API funcionando 🚀"

# Evitar que el navegador muestre páginas "viejas" tras cambios directos en DB
@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

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

    usuarios_list = Usuario.query.order_by(Usuario.id.asc()).all()
    roles = ['Admin', 'Presidente', 'Vicepresidente', 'Asistente']
    return render_template('usuarios.html', usuarios=usuarios_list, roles=roles)


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

class Vecino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(100), nullable=False)
    telefono = db.Column(db.String(20))
    domicilio = db.Column(db.String(200), nullable=False)
    rut = db.Column(db.String(20), unique=True, nullable=False)
    fecha_registro = db.Column(db.DateTime, default=db.func.current_timestamp())
    notas = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)

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
    return Usuario.query.get(int(user_id))

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
    # Parámetros de búsqueda y filtros
    search = request.args.get('search', '').strip()
    sort_by = request.args.get('sort_by', 'nombre')
    sort_order = request.args.get('sort_order', 'asc')
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Vecinos por página
    
    # Query base (solo vecinos activos)
    query = Vecino.query.filter_by(activo=True)
    
    # Aplicar búsqueda si existe
    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                Vecino.nombre.ilike(search_term),
                Vecino.apellidos.ilike(search_term),
                Vecino.rut.ilike(search_term),
                Vecino.domicilio.ilike(search_term)
            )
        )
    
    # Aplicar ordenamiento
    if sort_by == 'nombre':
        if sort_order == 'asc':
            query = query.order_by(Vecino.nombre.asc())
        else:
            query = query.order_by(Vecino.nombre.desc())
    elif sort_by == 'apellidos':
        if sort_order == 'asc':
            query = query.order_by(Vecino.apellidos.asc())
        else:
            query = query.order_by(Vecino.apellidos.desc())
    elif sort_by == 'rut':
        if sort_order == 'asc':
            query = query.order_by(Vecino.rut.asc())
        else:
            query = query.order_by(Vecino.rut.desc())
    elif sort_by == 'domicilio':
        if sort_order == 'asc':
            query = query.order_by(Vecino.domicilio.asc())
        else:
            query = query.order_by(Vecino.domicilio.desc())
    elif sort_by == 'fecha_registro':
        if sort_order == 'asc':
            query = query.order_by(Vecino.fecha_registro.asc())
        else:
            query = query.order_by(Vecino.fecha_registro.desc())
    else:
        # Por defecto ordenar por nombre ascendente
        query = query.order_by(Vecino.nombre.asc())
    
    # Aplicar paginación
    pagination = query.paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    vecinos = pagination.items
    
    # Calcular estadísticas
    total_vecinos = Vecino.query.filter_by(activo=True).count()
    vecinos_filtrados = query.count()
    
    return render_template('dashboard.html', 
                         vecinos=vecinos,
                         pagination=pagination,
                         search=search,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         total_vecinos=total_vecinos,
                         vecinos_filtrados=vecinos_filtrados)

@app.route('/exportar-excel')
@login_required
def exportar_excel():
    vecinos = Vecino.query.filter_by(activo=True).order_by(Vecino.nombre.asc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Vecinos'
    # Encabezados
    ws.append(['#', 'Nombre', 'Apellidos', 'RUT', 'Domicilio', 'Teléfono', 'Fecha Registro', 'Notas'])
    # Datos
    for idx, v in enumerate(vecinos, 1):
        ws.append([
            idx,
            v.nombre,
            v.apellidos,
            v.rut,
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
    search = request.args.get('search', '').strip()
    pago_filtro = (request.args.get('pago') or '').strip().upper()  # SI|NO|''
    sort_by = request.args.get('sort_by', 'fecha')
    sort_order = request.args.get('sort_order', 'desc')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = CertificadoResidencia.query.filter_by(activo=True)

    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                CertificadoResidencia.nombres.ilike(search_term),
                CertificadoResidencia.apellidos.ilike(search_term),
                CertificadoResidencia.rut.ilike(search_term),
                CertificadoResidencia.direccion.ilike(search_term)
            )
        )

    if pago_filtro in {'SI', 'NO'}:
        query = query.filter(CertificadoResidencia.pago == (pago_filtro == 'SI'))

    sort_col = {
        'fecha': CertificadoResidencia.fecha,
        'nombres': CertificadoResidencia.nombres,
        'apellidos': CertificadoResidencia.apellidos,
        'rut': CertificadoResidencia.rut,
        'direccion': CertificadoResidencia.direccion,
        'pago': CertificadoResidencia.pago,
        'fecha_creacion': CertificadoResidencia.fecha_creacion,
    }.get(sort_by, CertificadoResidencia.fecha)

    query = query.order_by(sort_col.asc() if sort_order == 'asc' else sort_col.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    certificados_items = pagination.items

    total_certificados = CertificadoResidencia.query.filter_by(activo=True).count()
    certificados_filtrados = query.count()

    return render_template(
        'certificados.html',
        certificados=certificados_items,
        pagination=pagination,
        search=search,
        pago=pago_filtro,
        sort_by=sort_by,
        sort_order=sort_order,
        total_certificados=total_certificados,
        certificados_filtrados=certificados_filtrados
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
    search = request.args.get('search', '').strip()
    sort_by = request.args.get('sort_by', 'fecha_creacion')
    sort_order = request.args.get('sort_order', 'desc')
    page = request.args.get('page', 1, type=int)
    per_page = 12

    query = Documento.query.filter_by(activo=True, tipo=tipo_norm)

    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                Documento.nombre.ilike(search_term),
                Documento.archivo_nombre.ilike(search_term)
            )
        )

    sort_col = {
        'nombre': Documento.nombre,
        'archivo': Documento.archivo_nombre,
        'fecha_creacion': Documento.fecha_creacion,
    }.get(sort_by, Documento.fecha_creacion)

    query = query.order_by(sort_col.asc() if sort_order == 'asc' else sort_col.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        'documentos_tipo.html',
        tipo=tipo_norm,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        documentos=pagination.items,
        pagination=pagination
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
        form_data = {
            'nombre': request.form['nombre'].strip(),
            'apellidos': request.form['apellidos'].strip(),
            'telefono': request.form['telefono'].strip(),
            'domicilio': request.form['domicilio'].strip(),
            'rut': rut,
            'notas': request.form['notas'].strip()
        }
        if not es_valido:
            flash(f'Error en RUT: {mensaje_error}', 'error')
            return render_template('nuevo_vecino.html', form_data=form_data)
        existe, vecino_existente = rut_existe(rut)
        if existe:
            flash(f'El RUT ya está registrado por {vecino_existente.nombre} {vecino_existente.apellidos}', 'error')
            return render_template('nuevo_vecino.html', form_data=form_data)
        rut_formateado = formatear_rut(rut)
        vecino = Vecino(
            nombre=form_data['nombre'],
            apellidos=form_data['apellidos'],
            telefono=form_data['telefono'],
            domicilio=form_data['domicilio'],
            rut=rut_formateado,
            notas=form_data['notas']
        )
        db.session.add(vecino)
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
    return render_template('nuevo_vecino.html', form_data=None)

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
        form_data = {
            'nombre': request.form['nombre'].strip(),
            'apellidos': request.form['apellidos'].strip(),
            'telefono': request.form['telefono'].strip(),
            'domicilio': request.form['domicilio'].strip(),
            'rut': rut,
            'notas': request.form['notas'].strip()
        }
        if not es_valido:
            flash(f'Error en RUT: {mensaje_error}', 'error')
            return render_template('editar_vecino.html', vecino=vecino, form_data=form_data)
        existe, vecino_existente = rut_existe(rut, vecino.id)
        if existe:
            flash(f'El RUT ya está registrado por {vecino_existente.nombre} {vecino_existente.apellidos}', 'error')
            return render_template('editar_vecino.html', vecino=vecino, form_data=form_data)
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
        if vecino.notas != form_data['notas']:
            cambios.append(f"Notas: '{vecino.notas}' → '{form_data['notas']}'")
        vecino.nombre = form_data['nombre']
        vecino.apellidos = form_data['apellidos']
        vecino.telefono = form_data['telefono']
        vecino.domicilio = form_data['domicilio']
        vecino.rut = rut_formateado
        vecino.notas = form_data['notas']
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
    return render_template('editar_vecino.html', vecino=vecino, form_data=None)

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

    usuario = (request.args.get('usuario') or '').strip()
    desde = (request.args.get('desde') or '').strip()  # YYYY-MM-DD
    hasta = (request.args.get('hasta') or '').strip()  # YYYY-MM-DD

    query = RegistroMovimiento.query

    if usuario:
        term = f"%{usuario}%"
        if usuario.isdigit():
            query = query.filter(
                db.or_(
                    RegistroMovimiento.usuario_id == int(usuario),
                    RegistroMovimiento.usuario_nombre.ilike(term)
                )
            )
        else:
            query = query.filter(RegistroMovimiento.usuario_nombre.ilike(term))

    # Filtro por fecha (rango). Si llega solo una, se aplica como "desde" o "hasta".
    try:
        if desde:
            dt_desde = datetime.datetime.strptime(desde, "%Y-%m-%d")
            query = query.filter(RegistroMovimiento.fecha_hora >= dt_desde)
        if hasta:
            dt_hasta = datetime.datetime.strptime(hasta, "%Y-%m-%d")
            dt_hasta = dt_hasta.replace(hour=23, minute=59, second=59, microsecond=999999)
            query = query.filter(RegistroMovimiento.fecha_hora <= dt_hasta)
    except ValueError:
        flash('Formato de fecha inválido. Usa YYYY-MM-DD.', 'error')

    query = _registros_movimiento_solo_coherentes(query)

    pagination = query.order_by(RegistroMovimiento.fecha_hora.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    return render_template(
        'registros.html',
        pagination=pagination,
        registros=pagination.items,
        usuario=usuario,
        desde=desde,
        hasta=hasta
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

if __name__ == '__main__':
    with app.app_context():
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

        # Migración simple: asegurar columna Vecino.activo exista
        try:
            inspector = db.inspect(db.engine)
            if inspector.has_table('vecino'):
                cols = {c['name'] for c in inspector.get_columns('vecino')}
                if 'activo' not in cols:
                    db.session.execute(db.text("ALTER TABLE vecino ADD COLUMN activo TINYINT(1) NOT NULL DEFAULT 1"))
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
    
    import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)