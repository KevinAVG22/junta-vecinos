# Descripción detallada de la web (JuntaDeVecinos)

Este documento describe **de forma explícita** la aplicación web contenida en este repositorio, incluyendo **pantallas**, **funcionalidades**, **rutas**, **datos almacenados**, **validaciones**, **roles/permisos**, **archivos** y **comportamientos relevantes**.

## 1) ¿Qué es esta web?

Aplicación web para **gestión comunitaria** de la **Junta De Vecino Colón Oriente** (Las Condes, Santiago, Chile), construida con **Python + Flask**, persistencia en **MySQL** (típicamente vía XAMPP) y UI con **Tailwind CSS**.

Su objetivo es permitir, bajo inicio de sesión:

- Administrar un **padrón/listado de vecinos** (crear, buscar, editar, eliminar, ver ficha expandible).
- Visualizar **ubicación geográfica** de vecinos en un **mapa interactivo del sector** (Leaflet + geocodificación).
- Consultar **personas agrupadas por casa/domicilio**.
- Emitir y gestionar **Certificados de Residencia**, con **vista previa** y **PDF generado** automáticamente.
- Administrar un repositorio de **Documentos** subidos (archivos) organizados por **Tipos**.
- (Solo administradores) Administrar **Usuarios y roles**, y consultar **Historial** (auditoría de movimientos/acciones).

## 2) Tecnologías y dependencias principales

Según `requirements.txt`:

- **Flask**: servidor web y ruteo.
- **Flask-SQLAlchemy**: ORM y conexión a MySQL.
- **Flask-Login**: sesiones, login/logout, protección de rutas.
- **Flask-WTF / WTForms**: formularios (dependencias declaradas).
- **PyMySQL**: driver MySQL.
- **python-dotenv**: carga de `.env` para `SECRET_KEY` y variables del mapa.
- **Werkzeug**: hashing de contraseñas y utilidades.
- **openpyxl**: exportación de vecinos a Excel (`.xlsx`).
- **playwright**: generación de PDF (Chromium headless) desde HTML.
- **reportlab / pillow / charset-normalizer**: dependencias auxiliares del ecosistema PDF/imágenes.

**Frontend (CDN, sin build step):**

- **Tailwind CSS** (modo claro/oscuro con clase `dark`).
- **Leaflet 1.9.4** + **Leaflet.markercluster** (mapa del sector y picker de ubicación).
- **OpenStreetMap** (tiles del mapa).

## 3) Configuración y ejecución

### 3.1 Configuración de Flask y DB

En `app.py`:

- **SECRET_KEY**: se toma desde `SECRET_KEY` en `.env`. Si no existe, usa un fallback.
- **Base de datos**: `mysql+pymysql://root:@localhost/junta_vecinos`
  - Usuario: `root`
  - Password: vacío (por defecto)
  - Host: `localhost`
  - DB: `junta_vecinos`
- **Uploads**:
  - Carpeta: `uploads/` en el raíz de la app (se crea automáticamente si no existe).
  - Límite de tamaño: **16MB** por request (`MAX_CONTENT_LENGTH`).

### 3.2 Variables de entorno del mapa (opcionales)

Configurables en `.env` (valores por defecto orientados al cuadrante Colón Oriente):

| Variable | Descripción | Default |
|----------|-------------|---------|
| `MAP_SECTOR_CONTEXT` | Contexto textual del sector | `Colón Oriente, Las Condes, Santiago, Chile` |
| `MAP_CENTER_LAT` / `MAP_CENTER_LNG` | Centro del mapa | `-33.4143` / `-70.5370` |
| `MAP_BOUNDS_NORTH/SOUTH/WEST/EAST` | Límites del cuadrante | ver `app.py` |
| Cuadrante de calles | Cristóbal Colón, Padre Hurtado Sur, Río Guadiana y Paul Harris | constante en código |

### 3.3 Primer arranque (migraciones simples y usuario admin)

Al iniciar la app, `@app.before_request` ejecuta `_ensure_db_schema()` una sola vez:

- `db.create_all()` (crea tablas si no existen).
- **Migraciones simples** (ALTER TABLE) para asegurar columnas:
  - `usuario.role`
  - `vecino.activo`
  - `vecino.fecha_nacimiento`
  - `vecino.latitud`, `vecino.longitud`, `vecino.geocodificado_en`, `vecino.geocodificacion_error`, `vecino.domicilio_mapeado`
  - `certificado_residencia.pago`
  - `certificado_residencia.presentado_en`
  - `certificado_residencia.documento_id`
- Backfill de `domicilio_mapeado` para vecinos ya geocodificados.
- Se crea un **usuario administrador por defecto** si no existe:
  - **username**: `admin`
  - **password**: `admin123`
  - **email**: `admin@junta.com`
  - rol: `Admin`

Al ejecutar `python app.py` directamente, el servidor corre en `http://0.0.0.0:5000` (accesible en LAN).

## 4) Navegación y layout (UI)

### 4.1 Layout base y componentes comunes

Plantilla: `templates/base.html`

- **Tailwind via CDN** con color primario `#059669` (verde).
- **Modo claro/oscuro**:
  - Botón “Claro / Oscuro” en header (usuarios autenticados) y navbar (no autenticados).
  - Persistencia en `localStorage` con clave `theme`.
  - Por defecto respeta el `prefers-color-scheme` del sistema si no hay preferencia guardada.
- **Sidebar (autenticados)**:
  - Se puede colapsar/expandir.
  - Persistencia en `localStorage` con clave `sidebar` (`collapsed|expanded`).
  - Título: “Junta De Vecino Colon Oriente”.
- **Flash messages**: mensajes de éxito/error/info con colores.
- **Logo**: `static/junta de vecinos.jpg`.
- **Resaltado de búsqueda**: estilos `mark.dt-highlight` y `td.dt-cell-match` para coincidencias en tablas.

### 4.2 Menú lateral (autenticados)

En sesión aparecen accesos a:

- **Dashboard (Vecinos)**: `/dashboard`
- **Mapa del Sector**: `/mapa`
- **Personas por Casa**: `/casas`
- **Certificados de residencia**: `/certificados`
- **Documentos**: `/documentos`
- **Mi cuenta**: `/mi-cuenta`
- (Solo Admin) **Historial**: `/registros`
- (Solo Admin) **Usuarios**: `/usuarios`
- **Cerrar sesión**: `/logout`

### 4.3 Macros reutilizables de tablas

Plantilla: `templates/macros/data_table.html`

Macros compartidos por dashboard, casas, certificados, documentos, registros y usuarios:

- `filter_input`, `sort_button`, `th_label`, `th_filter`, `dt_cell`
- `table_toolbar`, `pagination_nav`, `table_filter_script`
- Orden por clic en cabecera, búsqueda con auto-submit (debounce ~1 s), resaltado de términos.

Otras macros:

- `macros/vecino_detail.html` — panel expandible de vecino + certificados vinculados + eliminar.
- `macros/casa_detail.html` — panel expandible de integrantes por casa.
- `macros/vecino_ubicacion_mapa.html` — geocodificación y picker manual en formularios de vecino.
- `macros/certificado_preview.html` — modal de vista previa de certificados.

### 4.4 Filtros Jinja personalizados

En `app.py`:

- `highlight` — resalta coincidencias de búsqueda (incluye normalización de RUT).
- `matches_term` — indica si un texto coincide con el término buscado.
- `merge_dicts` — fusiona diccionarios en plantillas.

## 5) Roles y permisos

El modelo `Usuario` incluye:

- `es_admin` (booleano)
- `role` (string) con valores esperados: `Admin`, `Presidente`, `Vicepresidente`, `Asistente`

Reglas de permisos implementadas:

- **Administración de usuarios**: solo Admin (`/usuarios`, crear usuario, cambiar rol, reset contraseña).
- **Historial / auditoría**: solo Admin (`/registros`).
- El resto de secciones (vecinos, mapa, casas, certificados, documentos, mi cuenta) requieren estar autenticado, pero **no** restringen por rol (más allá de lo anterior).

## 6) Modelos / datos almacenados (tablas)

La base de datos contiene (ORM en `app.py`):

### 6.1 `usuario` (modelo `Usuario`)

- `id` (PK, int)
- `username` (string, único, requerido)
- `email` (string, único, requerido)
- `password_hash` (string, requerido)
- `es_admin` (boolean)
- `role` (string, por defecto `Asistente`)

**Contraseñas**: almacenadas hasheadas con Werkzeug.

### 6.2 `vecino` (modelo `Vecino`)

- `id` (PK, int)
- `nombre` (string, requerido)
- `apellidos` (string, requerido)
- `telefono` (string, opcional)
- `domicilio` (string, requerido)
- `rut` (string, único, requerido) — se guarda **formateado**
- `fecha_nacimiento` (date, opcional)
- `fecha_registro` (datetime, default timestamp)
- `notas` (text, opcional)
- `activo` (boolean, default true)
- `latitud`, `longitud` (float, opcionales) — coordenadas en el mapa
- `geocodificado_en` (datetime, opcional) — última geocodificación
- `geocodificacion_error` (string, opcional) — error o aviso (ej. fuera de cuadrante)
- `domicilio_mapeado` (string, opcional) — domicilio normalizado usado al geocodificar

**Propiedades calculadas (no persistidas):**

- `edad` — años completos según `fecha_nacimiento` y fecha actual.
- `rango_etario` — categoría: `0-17`, `18-29`, `30-44`, `45-59`, `60-74`, `75+`, `Sin dato`.

**Nota sobre borrado**: la UI trabaja con activos; la ruta de eliminación hace **hard delete** definitivo.

### 6.3 `registro_accion` (modelo `RegistroAccion`)

Registro histórico asociado a vecinos:

- `id`, `usuario_id`, `usuario_nombre`, `vecino_id`
- `accion` (`crear|editar|eliminar|ver`)
- `fecha_hora`, `detalles`

### 6.4 `registro_movimiento` (modelo `RegistroMovimiento`)

Auditoría general usada para “Historial”:

- `id`, `usuario_id`, `usuario_nombre`
- `entidad` (`vecino|certificado|documento|tipo_documento|usuario`)
- `entidad_id`
- `accion` (`crear|editar|eliminar|ver|descargar`)
- `fecha_hora`, `detalles`

En la vista de historial se filtran movimientos “huérfanos” para no mostrar registros inconsistentes si se borró info directamente en MySQL (excepto acciones `eliminar`).

### 6.5 `certificado_residencia` (modelo `CertificadoResidencia`)

- `id`, `fecha`, `nombres`, `apellidos`, `rut`, `direccion`
- `presentado_en` (opcional en DB; requerido en formulario)
- `pago` (boolean, default false)
- `archivo_nombre`, `archivo_ruta` — presentes en el modelo; la vinculación real usa `documento_id`
- `documento_id` (int, referencia lógica al Documento generado)
- `activo` (boolean, default true)
- `fecha_creacion` (datetime)

### 6.6 `documento` (modelo `Documento`)

- `id`, `nombre`, `tipo`, `archivo_nombre`, `archivo_ruta`
- `activo`, `fecha_creacion`

### 6.7 `documento_tipo` (modelo `DocumentoTipo`)

Catálogo de tipos con nombre único. Tipo reservado: **Certificados de residencia** (PDFs generados automáticamente; no se puede eliminar desde la UI si tiene documentos o es el tipo reservado).

## 7) Validaciones y normalizaciones importantes

### 7.1 Validación de RUT chileno

Función `validar_rut(rut)`: limpia, verifica largo, calcula DV por módulo 11.

### 7.2 Formateo de RUT

Función `formatear_rut(rut)`: inserta puntos y guión (ej. `12.345.678-K`).

### 7.3 Unicidad de RUT en vecinos

Función `rut_existe(rut, excluir_id=None)`: compara RUT normalizado contra vecinos activos.

### 7.4 Fecha de nacimiento

Función `_parse_fecha_nacimiento(value)`:

- Acepta `YYYY-MM-DD`, `DD-MM-YYYY` o `DD/MM/YYYY`.
- Rechaza fechas futuras.
- Campo opcional en formularios.

### 7.5 Normalización de domicilios (geocodificación)

Función `_normalizar_domicilio_geocodificacion(domicilio)`: corrige variantes comunes de calles del sector (Sierra Nevada, Río Guadiana, Paul Harris, Cristóbal Colón, etc.).

Función `_normalize_domicilio_key(domicilio)`: clave para agrupar casas (elimina puntos, guiones y espacios, minúsculas).

### 7.6 Cuadrante geográfico

Función `_coords_dentro_cuadrante(lat, lng)`: valida que las coordenadas estén dentro de los límites configurados del sector.

### 7.7 Tipos de archivo permitidos

- Documentos genéricos: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`
- Vista previa inline: solo `.pdf`, `.png`, `.jpg`, `.jpeg`

## 8) Geocodificación y mapa

### 8.1 Servicios externos

- **Nominatim** (OpenStreetMap): geocodificación principal con rate limit ~1.25 s entre consultas.
- **Photon** (Komoot): geocodificación alternativa con rate limit ~0.35 s.

User-Agent: `JuntaDeVecinosColonOriente/1.0 (Las Condes, Chile)`.

### 8.2 Flujo de ubicación de vecinos

Al crear/editar vecino:

1. **Automático**: botón “Obtener Coordenadas Automáticamente” → `POST /api/geocodificar-domicilio`.
2. **Manual**: botón “Seleccionar en el Mapa” → modal Leaflet con pins azules (domicilios existentes) y pin verde (selección). API `GET /api/mapa/referencias-picker` (soporta `excluir_id` al editar).
3. Al confirmar pin existente (≤ 25 m), autocompleta el campo domicilio.
4. Si cambia el domicilio sin coords manuales, se re-geocodifica al guardar.

### 8.3 Sincronización masiva

- `POST /api/mapa/geocodificar` — inicia hilo en segundo plano para geocodificar vecinos pendientes (`force=1` reintenta todos).
- `GET /api/mapa/sincronizacion` — estado de progreso (`running`, `total`, `done`, `ok`, `fail`, `pendientes`).

### 8.4 Agrupación de marcadores

Vecinos con misma clave de domicilio + coordenadas redondeadas se agrupan en un solo pin con badge de cantidad.

## 9) Funcionalidades por módulo (pantallas y rutas)

### 9.1 Página pública (sin sesión)

#### `GET /`

- Renderiza `templates/index.html` (landing).
- Navbar con enlace a login y toggle de tema.

### 9.2 Autenticación

#### `GET|POST /login`

- Redirige a `/dashboard` si ya hay sesión.
- POST: valida username/password, inicia sesión con Flask-Login.

#### `GET /logout`

- Cierra sesión y redirige a `/`.

### 9.3 Dashboard de Vecinos (CRUD + búsqueda + exportación + demografía)

Pantalla: `templates/dashboard.html`

#### `GET /dashboard`

Funcionalidades:

- **Listado tabular** de vecinos activos con columnas:
  - # (correlativo con paginación)
  - Nombre, Apellidos, RUT, Domicilio, Teléfono
  - **F. Nacimiento**, **Edad**
  - Fecha registro
  - Acciones: Editar / Eliminar
- **Fila expandible**: clic en fila muestra panel con ficha completa, notas, rango etario, certificados vinculados por RUT y acciones.
- **Búsqueda global** (`q`) por: nombre, apellidos, rut, domicilio, teléfono, notas (RUT también sin puntos/guión).
- **Ordenamiento** por: `nombre`, `apellidos`, `rut`, `domicilio`, `telefono`, `fecha_nacimiento`, `fecha_registro` (asc/desc).
- **Paginación**: 10 vecinos por página.
- **Panel de rangos etarios**: conteo por categoría (`0-17` … `75+`, `Sin dato`) con porcentaje sobre el total.
- **Estadísticas**: total vecinos, mostrando (filtrado), página actual.
- **Acciones rápidas**: Exportar Excel, Agregar Vecino, (Admin) Historial.
- **Eliminar**: botón con confirmación vía `data-confirm` + listener JS `.btn-eliminar-vecino` (evita problemas de comillas en `onclick`).

#### `GET /exportar-excel`

Genera `vecinos.xlsx` con columnas:

- `#`, `Nombre`, `Apellidos`, `RUT`, `Fecha Nacimiento`, `Edad`, `Rango Etario`, `Domicilio`, `Teléfono`, `Fecha Registro`, `Notas`

Orden: por nombre ASC. Fechas en formato `dd/mm/YYYY`.

#### `GET|POST /vecinos/nuevo`

Pantalla: `templates/nuevo_vecino.html`

Campos:

- `nombre`, `apellidos` (obligatorios)
- `telefono`, `notas` (opcionales)
- `domicilio` (obligatorio)
- `fecha_nacimiento` (opcional)
- `rut` (obligatorio, validado y único)
- `latitud`, `longitud` (opcionales; macro de mapa)
- Sección **Ubicación en el Mapa** (`macros/vecino_ubicacion_mapa.html`)

Al crear: inserta vecino, aplica ubicación/geocodificación, registra auditoría.

#### `GET|POST /vecinos/<id>/editar`

Pantalla: `templates/editar_vecino.html`

- Pasa `vecino.id` al macro de mapa (`excluir_id` en referencias).
- Revalida RUT, fecha de nacimiento y cambios de domicilio/coords.
- Si hay coords manuales nuevas, no re-geocodifica automáticamente; si cambió domicilio sin coords, re-geocodifica.
- Registra cambios campo a campo en auditoría.

#### `GET /vecinos/<id>`

Pantalla: `templates/ver_vecino.html` — ficha de detalle del vecino.

#### `GET /vecinos/<id>/eliminar`

Hard delete del vecino + limpieza de `RegistroAccion` asociados + movimiento de auditoría.

### 9.4 Personas por Casa

Pantalla: `templates/casas.html`

#### `GET /casas`

Agrupa vecinos activos por domicilio normalizado (`_normalize_domicilio_key`):

- Domicilios escritos distinto (mayúsculas, puntos, espacios) pueden aparecer como casas distintas si la clave normalizada difiere.
- El domicilio mostrado es el más frecuente entre integrantes.

**Columnas**: Domicilio, Integrantes, Tipo (`Individual` / `Compartida`), Mapa (si algún integrante tiene ubicación).

**Estadísticas**:

- Total casas e integrantes
- Promedio por casa
- Casa más habitada
- Distribución: solas (1 persona) vs compartidas (2+)

**Búsqueda** (`q`): domicilio, calle, nombre/apellido o RUT de integrantes.

**Orden**: `domicilio`, `integrantes`, `tipo`, `mapa`.

**Paginación**: 10 casas por página (`SimplePagination` sobre lista en memoria).

**Fila expandible**: lista de integrantes con enlaces a editar/ver.

### 9.5 Mapa del Sector

Pantalla: `templates/mapa_sector.html`

#### `GET /mapa`

Mapa Leaflet a pantalla completa con:

- Cuadrante del sector delimitado.
- Clustering de marcadores (MarkerCluster).
- Pines con forma de casa al máximo zoom; clusters resumidos al alejar.
- Estadísticas: vecinos con ubicación, pines activos, estado.
- Controles: centrar, mostrar/ocultar pines, filtro (`todos|con_ubicacion|sin_ubicacion`), recargar datos.
- Panel de vecinos sin ubicación.
- Modal con detalle de domicilio e integrantes al hacer clic en pin.

#### `GET /api/mapa/datos`

JSON con configuración del mapa, estadísticas, marcadores agrupados y lista `sin_ubicacion`. Parámetro `filtro`.

#### `GET /api/mapa/referencias-picker`

Marcadores simplificados (domicilio, lat, lng) para el picker de formularios. Parámetro `excluir_id`.

#### `POST /api/geocodificar-domicilio`

Geocodifica un domicilio puntual; retorna lat/lng, si está en cuadrante, avisos.

#### `GET /api/mapa/sincronizacion`

Estado del job de sincronización masiva.

#### `POST /api/mapa/geocodificar`

Inicia sincronización masiva de ubicaciones pendientes.

### 9.6 Certificados de Residencia (CRUD + PDF + vista previa)

Pantallas: `certificados.html`, `nuevo_certificado.html`, `editar_certificado.html`, `certificado_plantilla.html`

#### `GET /certificados`

Listado con **filtros por columna** (macros data-table):

- `f_fecha`, `f_nombres`, `f_apellidos`, `f_rut`, `f_direccion`, `f_pago` (`SI|NO|Todos`)
- Orden: fecha, nombres, apellidos, rut, dirección, pago, fecha_creacion
- Paginación: 10 por página
- Acciones: Vista previa (modal iframe), Descargar PDF, Editar, Eliminar

#### `GET|POST /certificados/nuevo`

Campos: fecha, nombres, apellidos, rut, dirección, presentado_en, pago.

Al crear: inserta certificado, genera PDF con Playwright, crea Documento vinculado, registra auditoría. Soporta `next` interno.

#### `GET|POST /certificados/<id>/editar`

Actualiza campos y regenera PDF (actualiza o crea Documento).

#### `GET /certificados/<id>/imprimir`

HTML de plantilla para impresión/vista previa (`embed=1` para iframe).

#### `GET /certificados/<id>/ver`

Vista previa embebible: sirve el PDF si existe; si no, plantilla HTML.

#### `GET /certificados/<id>/pdf`

Descarga del PDF vinculado.

#### `GET /certificados/<id>/eliminar`

Hard delete del certificado + Documento asociado + archivo físico.

### 9.7 Documentos (subida, tipos, agrupación, descarga, vista previa)

Pantallas: `documentos.html`, `documentos_tipos.html`, `nuevo_documento.html`, `documentos_tipo.html`

#### `GET /documentos`

Cards por tipo (incluye tipos con 0 documentos). Botones subir y administrar tipos.

#### `GET|POST /documentos/tipos`

CRUD de tipos (soft delete = desactivar).

#### `GET /documentos/tipos/<id>/eliminar` (Admin)

Desactiva tipo si no es reservado y no tiene documentos activos.

#### `GET|POST /documentos/nuevo`

Subida genérica con validación de extensión. Tipo reservado redirige al módulo de certificados.

#### `GET /documentos/tipo/<tipo>`

Listado paginado (12/página) con búsqueda y orden.

#### `GET /documentos/<id>/archivo` — descarga attachment.

#### `GET /documentos/<id>/ver` — vista previa inline (PDF/imagen).

#### `GET /documentos/<id>/eliminar` — hard delete + archivo físico. Soporta `?next=`.

### 9.8 Usuarios (solo Admin)

Pantalla: `templates/usuarios.html`

#### `GET /usuarios`

Lista con filtros: `f_username`, `f_email`, `f_role`.

#### `POST /usuarios/nuevo`, `POST /usuarios/<id>/rol`, `POST /usuarios/<id>/reset-password`

Gestión de usuarios con validaciones de rol y password mínimo 6 caracteres.

### 9.9 Mi cuenta

#### `GET|POST /mi-cuenta`

Cambio de contraseña propia con verificación de contraseña actual.

### 9.10 Historial / auditoría (solo Admin)

Pantalla: `templates/registros.html`

#### `GET /registros`

Listado paginado (20/página) con filtros:

- `f_usuario` (id o nombre)
- `f_desde`, `f_hasta` (YYYY-MM-DD)
- `f_accion`, `f_entidad`, `f_detalles`
- Orden: `fecha_hora`, `usuario`, `accion`, `entidad`

### 9.11 Validación de RUT (utilidad)

#### `GET|POST /validar-rut`

Página de prueba manual.

#### `POST /api/verificar-rut`

API JSON: `{ rut, excluir_id? }` → `{ valido, mensaje }`.

## 10) Archivos físicos y generación de PDFs

### 10.1 Carpeta de uploads

- `uploads/` en la raíz de la app.

Nomenclatura:

- Documentos genéricos: `doc_<timestamp>_<archivo_sanitizado>`
- PDFs de certificados: `certhtml_<timestamp>_<archivo_sanitizado>.pdf`

### 10.2 Generación de PDF de certificado

- Renderiza `certificado_plantilla.html` con datos del certificado.
- Logo embebido en Base64 si existe `static/junta de vecinos.jpg`.
- Playwright Chromium headless → PDF A4, `print_background=True`, márgenes 0.
- Se registra como `Documento` del tipo reservado.

## 11) Estructura de archivos del proyecto

```
JuntaDeVecinos/
├── app.py                    # Aplicación Flask (modelos, rutas, lógica)
├── requirements.txt
├── config.env                # Plantilla de variables (.env)
├── web.md                    # Este documento
├── README.md
├── setup.py
├── static/
│   └── junta de vecinos.jpg  # Logo
├── uploads/                  # Archivos subidos/generados (gitignored parcialmente)
└── templates/
    ├── base.html
    ├── index.html, login.html
    ├── dashboard.html
    ├── casas.html
    ├── mapa_sector.html
    ├── nuevo_vecino.html, editar_vecino.html, ver_vecino.html
    ├── certificados.html, nuevo_certificado.html, editar_certificado.html
    ├── certificado_plantilla.html
    ├── documentos.html, documentos_tipos.html, documentos_tipo.html, nuevo_documento.html
    ├── usuarios.html, mi_cuenta.html, registros.html
    └── macros/
        ├── data_table.html
        ├── vecino_detail.html
        ├── casa_detail.html
        ├── vecino_ubicacion_mapa.html
        └── certificado_preview.html
```

## 12) Comportamientos/decisiones relevantes

- **Sin cache del navegador**: headers `no-store/no-cache` en cada respuesta.
- **Soft vs hard delete**:
  - Vecinos, certificados y documentos: hard delete en rutas de eliminación.
  - Tipos de documento: soft delete (`activo=False`).
- **Consistencia del historial**: oculta movimientos huérfanos.
- **Paginación en memoria**: `SimplePagination` + `_paginate_list` para listas derivadas (casas); `iter_pages()` con lógica `last` estilo Flask-SQLAlchemy (evita múltiples `…` consecutivos).
- **Certificados por vecino**: en dashboard se vinculan certificados activos por RUT normalizado.
- **Geocodificación fuera de cuadrante**: se guardan coords pero se registra aviso en `geocodificacion_error`.
- **No hay flag DB** que distinga ubicación manual vs automática; las referencias del picker usan todos los vecinos con lat/lng.

## 13) Resumen rápido de endpoints (mapa)

Públicos:

- `GET /` inicio
- `GET|POST /login` login
- `GET /validar-rut` (y `POST`) utilidad
- `POST /api/verificar-rut` API

Autenticados:

- `GET /logout`
- `GET /dashboard`
- `GET /exportar-excel`
- `GET|POST /vecinos/nuevo`
- `GET|POST /vecinos/<id>/editar`
- `GET /vecinos/<id>`
- `GET /vecinos/<id>/eliminar`
- `GET /casas`
- `GET /mapa`
- `GET /api/mapa/datos`
- `GET /api/mapa/referencias-picker`
- `POST /api/geocodificar-domicilio`
- `GET /api/mapa/sincronizacion`
- `POST /api/mapa/geocodificar`
- `GET /certificados`
- `GET|POST /certificados/nuevo`
- `GET|POST /certificados/<id>/editar`
- `GET /certificados/<id>/imprimir`
- `GET /certificados/<id>/ver`
- `GET /certificados/<id>/pdf`
- `GET /certificados/<id>/eliminar`
- `GET /documentos`
- `GET|POST /documentos/tipos`
- `GET /documentos/tipos/<id>/eliminar` (Admin)
- `GET|POST /documentos/nuevo`
- `GET /documentos/tipo/<tipo>`
- `GET /documentos/<id>/archivo`
- `GET /documentos/<id>/ver`
- `GET /documentos/<id>/eliminar`
- `GET|POST /mi-cuenta`

Solo Admin:

- `GET /usuarios`
- `POST /usuarios/nuevo`
- `POST /usuarios/<id>/rol`
- `POST /usuarios/<id>/reset-password`
- `GET /registros`
