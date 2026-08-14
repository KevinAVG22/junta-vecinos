# Descripción detallada de la web (JuntaDeVecinos)

Este documento describe **de forma explícita** la aplicación web contenida en este repositorio, incluyendo **pantallas**, **funcionalidades**, **rutas**, **datos almacenados**, **validaciones**, **roles/permisos**, **archivos** y **comportamientos relevantes**.

## 1) ¿Qué es esta web?

Aplicación web para **gestión comunitaria** (Junta de Vecinos) construida con **Python + Flask**, con persistencia en **MySQL** (típicamente vía XAMPP) y UI con **Tailwind CSS**.

Su objetivo es permitir, bajo inicio de sesión:

- Administrar un **padrón/listado de vecinos** (crear, buscar, editar, eliminar, ver ficha).
- Emitir y gestionar **Certificados de Residencia**, con **vista previa** y **PDF generado** automáticamente.
- Administrar un repositorio de **Documentos** subidos (archivos) organizados por **Tipos**.
- (Solo administradores) Administrar **Usuarios y roles**, y consultar **Historial** (auditoría de movimientos/acciones).

## 2) Tecnologías y dependencias principales

Según `requirements.txt`:

- **Flask**: servidor web y ruteo.
- **Flask-SQLAlchemy**: ORM y conexión a MySQL.
- **Flask-Login**: sesiones, login/logout, protección de rutas.
- **PyMySQL**: driver MySQL.
- **python-dotenv**: carga de `.env` para `SECRET_KEY`.
- **Werkzeug**: hashing de contraseñas y utilidades.
- **openpyxl**: exportación de vecinos a Excel (`.xlsx`).
- **playwright**: generación de PDF (Chromium headless) desde HTML.

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

### 3.2 Primer arranque (migraciones simples y usuario admin)

Al ejecutar `python app.py`, en el bloque `if __name__ == '__main__':`:

- Se ejecuta `db.create_all()` (crea tablas si no existen).
- Se intentan **migraciones simples** (ALTER TABLE) para asegurar columnas:
  - `usuario.role`
  - `vecino.activo`
  - `certificado_residencia.pago`
  - `certificado_residencia.presentado_en`
  - `certificado_residencia.documento_id`
- Se crea un **usuario administrador por defecto** si no existe:
  - **username**: `admin`
  - **password**: `admin123`
  - **email**: `admin@junta.com`
  - rol: `Admin`

## 4) Navegación y layout (UI)

### 4.1 Layout base y componentes comunes

Plantilla: `templates/base.html`

- **Tailwind via CDN**.
- **Modo claro/oscuro**:
  - Botón “Claro / Oscuro” en header (usuarios autenticados) y navbar (no autenticados).
  - Persistencia en `localStorage` con clave `theme`.
  - Por defecto respeta el `prefers-color-scheme` del sistema si no hay preferencia guardada.
- **Sidebar (autenticados)**:
  - Se puede colapsar/expandir.
  - Persistencia en `localStorage` con clave `sidebar` (`collapsed|expanded`).
- **Flash messages**:
  - Muestra mensajes de éxito/error/info con colores.
- **Logo**:
  - Se referencia `static/junta de vecinos.jpg`.

### 4.2 Menú lateral (autenticados)

En sesión aparecen accesos a:

- **Dashboard (Vecinos)**: `/dashboard`
- **Certificados de residencia**: `/certificados`
- **Documentos**: `/documentos`
- **Mi cuenta**: `/mi-cuenta`
- (Solo Admin) **Historial**: `/registros`
- (Solo Admin) **Usuarios**: `/usuarios`
- **Cerrar sesión**: `/logout`

## 5) Roles y permisos

El modelo `Usuario` incluye:

- `es_admin` (booleano)
- `role` (string) con valores esperados: `Admin`, `Presidente`, `Vicepresidente`, `Asistente`

Reglas de permisos implementadas:

- **Administración de usuarios**: solo Admin (`/usuarios`, crear usuario, cambiar rol, reset contraseña).
- **Historial / auditoría**: solo Admin (`/registros`).
- El resto de secciones (vecinos, certificados, documentos, mi cuenta) requieren estar autenticado, pero **no** restringen por rol (más allá de lo anterior).

## 6) Modelos / datos almacenados (tablas)

La base de datos contiene (ORM en `app.py`):

### 6.1 `usuario` (modelo `Usuario`)

- `id` (PK, int)
- `username` (string, único, requerido)
- `email` (string, único, requerido)
- `password_hash` (string, requerido)
- `es_admin` (boolean)
- `role` (string, por defecto `Asistente`)

**Contraseñas**:

- Se almacenan hasheadas con Werkzeug (`generate_password_hash` / `check_password_hash`).

### 6.2 `vecino` (modelo `Vecino`)

- `id` (PK, int)
- `nombre` (string, requerido)
- `apellidos` (string, requerido)
- `telefono` (string, opcional)
- `domicilio` (string, requerido)
- `rut` (string, único, requerido) — se guarda **formateado**.
- `fecha_registro` (datetime, default timestamp)
- `notas` (text, opcional)
- `activo` (boolean, default true) — la UI trabaja con activos; el borrado actual es hard delete.

### 6.3 `registro_accion` (modelo `RegistroAccion`)

Registro “histórico viejo” asociado a vecinos:

- `id` (PK)
- `usuario_id`, `usuario_nombre`
- `vecino_id`
- `accion` (`crear|editar|eliminar|ver`)
- `fecha_hora`
- `detalles`

### 6.4 `registro_movimiento` (modelo `RegistroMovimiento`)

Auditoría más general, usada para “Historial”:

- `id` (PK)
- `usuario_id`, `usuario_nombre`
- `entidad` (`vecino|certificado|documento|tipo_documento|usuario`)
- `entidad_id`
- `accion` (`crear|editar|eliminar|ver|descargar`)
- `fecha_hora`
- `detalles`

En la vista de historial se filtran movimientos “huérfanos” para no mostrar registros inconsistentes si se borró info directamente en MySQL (excepto acciones `eliminar`).

### 6.5 `certificado_residencia` (modelo `CertificadoResidencia`)

- `id` (PK)
- `fecha` (date, requerido)
- `nombres` (string, requerido)
- `apellidos` (string, requerido)
- `rut` (string, requerido) — se valida y se guarda formateado.
- `direccion` (string, requerido)
- `presentado_en` (string, opcional en DB; requerido en formulario)
- `pago` (boolean, default false)
- `archivo_nombre`, `archivo_ruta` (strings) — presentes en el modelo, pero la vinculación real usa `documento_id`.
- `documento_id` (int, referencia lógica al Documento generado)
- `activo` (boolean, default true) — la UI lista activos; el borrado actual es hard delete.
- `fecha_creacion` (datetime, default timestamp)

### 6.6 `documento` (modelo `Documento`)

Representa un archivo subido o generado (PDF de certificado):

- `id` (PK)
- `nombre` (string, requerido)
- `tipo` (string, requerido) — coincide con `DocumentoTipo.nombre`
- `archivo_nombre` (string, requerido) — nombre original (sanitizado) del archivo
- `archivo_ruta` (string, requerido) — ruta física en `uploads/`
- `activo` (boolean, default true) — la UI lista activos; el borrado actual es hard delete.
- `fecha_creacion` (datetime)

### 6.7 `documento_tipo` (modelo `DocumentoTipo`)

Catálogo de tipos:

- `id` (PK)
- `nombre` (string, único, requerido)
- `activo` (boolean)
- `fecha_creacion` (datetime)

Existe un **tipo reservado**:

- `Certificados de residencia`

Este tipo se usa para guardar **automáticamente** los PDF generados desde certificados y se bloquea su eliminación desde la UI.

## 7) Validaciones y normalizaciones importantes

### 7.1 Validación de RUT chileno

Función `validar_rut(rut)`:

- Limpia puntos y guiones.
- Verifica largo mínimo.
- Separa número y dígito verificador (DV).
- Calcula DV por módulo 11.
- Devuelve `(True, None)` si es válido; si no, `(False, mensaje_error)`.

### 7.2 Formateo de RUT

Función `formatear_rut(rut)`:

- Limpia puntos y guiones.
- Inserta puntos cada 3 dígitos y guión antes del DV.
- Ejemplo conceptual: `12345678K` → `12.345.678-K`

### 7.3 Unicidad de RUT en vecinos

Función `rut_existe(rut, excluir_id=None)`:

- Compara RUT “limpio” (sin puntos/guión) contra vecinos activos.
- Sirve para:
  - **Crear vecino**: no permitir duplicados.
  - **Editar vecino**: permite conservar el propio RUT (usando `excluir_id`).

### 7.4 Tipos de archivo permitidos

- Para documentos genéricos (`_allowed_document_upload`):
  - `.pdf`, `.png`, `.jpg`, `.jpeg`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`
- Vista previa en navegador (`_documento_permite_vista_previa`):
  - Solo `.pdf`, `.png`, `.jpg`, `.jpeg`

## 8) Funcionalidades por módulo (pantallas y rutas)

### 8.1 Página pública (sin sesión)

#### `GET /`

- Renderiza `templates/index.html` (landing).
- Desde `base.html` (no autenticados) hay navbar con:
  - Enlace a login: `/login`
  - Toggle tema claro/oscuro

### 8.2 Autenticación

#### `GET|POST /login`

- Si ya estás autenticado, redirige a `/dashboard`.
- En POST:
  - Lee `username` y `password`.
  - Busca usuario por `username`.
  - Verifica password con hash.
  - Si ok: inicia sesión (`login_user`) y redirige a dashboard.
  - Si falla: muestra flash “Usuario o contraseña incorrectos”.

#### `GET /logout`

- Cierra sesión (`logout_user`) y redirige a `/`.

### 8.3 Dashboard de Vecinos (CRUD + búsqueda + exportación)

Pantalla: `templates/dashboard.html`

#### `GET /dashboard`

Funcionalidades:

- **Listado tabular** de vecinos activos con columnas:
  - # (correlativo considerando paginación)
  - Nombre, Apellidos, RUT, Domicilio, Teléfono, Fecha registro
  - Acciones: Ver / Editar / Eliminar
- **Búsqueda** (texto) por:
  - nombre, apellidos, rut, domicilio
- **Ordenamiento**:
  - por `nombre`, `apellidos`, `rut`, `domicilio`, `fecha_registro`
  - asc/desc
- **Paginación**:
  - 10 vecinos por página
- **Estadísticas** visibles:
  - Total vecinos (activos)
  - Mostrando (resultado filtrado)
  - Página actual / total páginas
- **Acciones rápidas (header)**:
  - “Exportar a Excel”
  - “+ Agregar Vecino”
  - (Solo Admin) “Historial”
- **UX extra**:
  - Auto-submit del buscador tras 1 segundo sin escribir.
  - Auto-submit al cambiar filtros.
  - Resaltado del término buscado en la tabla (con estilos distintos para modo oscuro).

#### `GET /exportar-excel`

Genera un Excel `vecinos.xlsx` con hoja `Vecinos`.

- Encabezados:
  - `#`, `Nombre`, `Apellidos`, `RUT`, `Domicilio`, `Teléfono`, `Fecha Registro`, `Notas`
- Orden: por `Vecino.nombre ASC`
- Fecha registro exportada como texto `dd/mm/YYYY`.
- Se devuelve como descarga (`send_file` en memoria).

#### `GET|POST /vecinos/nuevo`

Pantalla: `templates/nuevo_vecino.html`

Campos procesados:

- `nombre` (obligatorio)
- `apellidos` (obligatorio)
- `telefono` (opcional)
- `domicilio` (obligatorio)
- `rut` (obligatorio, validado y único)
- `notas` (opcional)

Reglas:

- Se valida RUT (DV correcto).
- Se impide duplicado de RUT (comparación normalizada).
- Se guarda RUT **formateado**.
- Al crear:
  - Inserta `Vecino`
  - Registra acción en `RegistroAccion` (crear)
  - Registra movimiento en `RegistroMovimiento` (entidad `vecino`, acción `crear`)

Resultado:

- Flash “Vecino agregado exitosamente”
- Redirige a `/dashboard`.

#### `GET|POST /vecinos/<id>/editar`

Pantalla: `templates/editar_vecino.html`

Comportamiento:

- En `GET`:
  - Registra “ver” (auditoría) indicando que accedió al formulario de edición.
- En `POST`:
  - Revalida RUT.
  - Verifica unicidad de RUT excluyendo el propio id.
  - Calcula lista de cambios (campo a campo) y la guarda en `detalles`.
  - Actualiza el vecino.
  - Registra en `RegistroAccion` y `RegistroMovimiento` como `editar`.

Resultado:

- Flash “Vecino actualizado exitosamente”
- Redirige a `/dashboard`.

#### `GET /vecinos/<id>`

Pantalla: `templates/ver_vecino.html`

- Muestra una ficha/detalle del vecino.
- Registra auditoría “ver”.

#### `GET /vecinos/<id>/eliminar`

- Elimina definitivamente (hard delete) el vecino.
- También borra registros en `RegistroAccion` asociados (por compatibilidad).
- Registra movimiento “eliminar”.
- Muestra confirmación en la UI (en dashboard hay `confirm()`).

### 8.4 Certificados de Residencia (CRUD + PDF + vista previa)

Pantallas:

- `templates/certificados.html` (listado)
- `templates/nuevo_certificado.html` (crear)
- `templates/editar_certificado.html` (editar)
- `templates/certificado_plantilla.html` (plantilla/preview/impresión)

#### `GET /certificados`

Listado con:

- Columnas:
  - #, Fecha, Nombres, Apellidos, RUT, Dirección, Pago, Acciones
- Filtros:
  - Búsqueda por nombres, apellidos, rut, dirección
  - Filtro por pago: `SI|NO|Todos`
  - Orden por: fecha, nombres, apellidos, rut, dirección, pago, fecha_creacion
  - Orden asc/desc
- Paginación: 10 por página
- Acciones por fila:
  - **Vista previa** (abre modal con iframe embebido)
  - **Descargar** (PDF)
  - **Editar**
  - **Eliminar**

#### `GET|POST /certificados/nuevo`

Campos (POST):

- `fecha` (formato HTML date `YYYY-MM-DD`, obligatorio)
- `nombres` (obligatorio)
- `apellidos` (obligatorio)
- `rut` (obligatorio, validado y formateado)
- `direccion` (obligatorio)
- `presentado_en` (obligatorio en formulario)
- `pago` (checkbox/valor; se interpreta como `si|sí|true|1|on`)

Al crear el certificado:

- Inserta `CertificadoResidencia`.
- Intenta generar PDF:
  - Asegura tipo `Certificados de residencia` en `DocumentoTipo`.
  - Genera PDF desde el **mismo HTML** de `certificado_plantilla.html` con Playwright (Chromium headless).
  - Guarda el PDF como archivo en `uploads/`.
  - Crea un registro `Documento` asociado y guarda `cert.documento_id`.
  - Registra movimiento para el `Documento`.
- Registra movimiento para el `Certificado` (crear).

Redirección:

- Soporta parámetro `next` (solo si comienza con `/`) para volver a una URL interna.

#### `GET|POST /certificados/<id>/editar`

- Revalida y actualiza campos.
- Regenera PDF:
  - Si ya existe `documento_id`, actualiza el Documento con el nuevo archivo.
  - Si no existe, crea el Documento y lo vincula.
- Registra movimientos (editar en certificado y crear/editar en documento).

#### `GET /certificados/<id>/imprimir`

Devuelve HTML de `certificado_plantilla.html`.

- Se usa para “vista previa” (en modal iframe) con query `embed=1`.
- Registra movimiento “ver”.

#### `GET /certificados/<id>/pdf`

Descarga el PDF vinculado (desde `Documento.archivo_ruta`).

- Si el archivo no existe, muestra error.
- Registra movimiento “descargar” tanto en certificado como en documento.

#### `GET /certificados/<id>/eliminar`

Eliminación definitiva (hard delete):

- Borra certificado y, si existe, también el Documento asociado.
- Intenta eliminar el archivo físico del PDF en `uploads/`.
- Registra movimientos “eliminar”.

### 8.5 Documentos (subida, tipos, agrupación, descarga, vista previa)

Pantallas:

- `templates/documentos.html` (cards por tipo)
- `templates/documentos_tipos.html` (admin de tipos)
- `templates/nuevo_documento.html` (subida)
- `templates/documentos_tipo.html` (listado por tipo)

#### `GET /documentos`

Muestra tarjetas (cards) por tipo:

- Lista **todos los tipos activos**, incluso si tienen 0 documentos.
- Cada card muestra:
  - nombre del tipo
  - cantidad de documentos activos en ese tipo
  - enlace para ver documentos del tipo
- Botones:
  - “+ Subir Documento” (`/documentos/nuevo`)
  - “Administrar tipos” (`/documentos/tipos`)
- (Solo Admin) en cada card aparece un botón para **eliminar tipo** (si cumple condiciones).

#### `GET|POST /documentos/tipos`

Permite:

- Crear un tipo nuevo (POST `nombre`).
- Si ya existe y estaba desactivado, lo reactiva.
- Lista tipos activos.
- Registra movimiento “crear” al agregar tipo.

#### `GET /documentos/tipos/<id>/eliminar` (solo Admin)

Desactiva (soft delete) un tipo si:

- No es el tipo reservado **Certificados de residencia**.
- No tiene documentos activos asociados.

Registra movimiento “eliminar” y marca `activo = False`.

#### `GET|POST /documentos/nuevo`

Subida de documento genérico (no certificado):

- Campos:
  - `nombre` (obligatorio)
  - `tipo` (obligatorio y debe existir como `DocumentoTipo` activo)
  - `archivo` (obligatorio; extensión permitida)
- Guardado:
  - Sanitiza nombre con `secure_filename`.
  - Guarda archivo en `uploads/` con prefijo timestamp.
  - Crea registro `Documento`.
  - Registra movimiento “crear”.

Caso especial:

- Si se intenta subir con tipo `Certificados de residencia`, la UI redirige/indica que esos se crean desde el módulo de certificados.
- En `GET /documentos/nuevo?tipo=<tipo>` precarga el tipo en el formulario (si no es el reservado).

#### `GET /documentos/tipo/<tipo>`

Listado de documentos de un tipo (paginado, estilo “grid/listado” según plantilla):

- Búsqueda por:
  - `Documento.nombre`
  - `Documento.archivo_nombre`
- Orden por:
  - `nombre`, `archivo`, `fecha_creacion`
- Paginación:
  - 12 por página

#### `GET /documentos/<id>/archivo`

Descarga del archivo adjunto (como attachment).

- Registra movimiento “descargar”.

#### `GET /documentos/<id>/ver`

Vista previa inline (solo PDF/imagenes).

- Si el archivo no es previsualizable, muestra aviso y redirige al listado.
- Registra movimiento “ver”.

#### `GET /documentos/<id>/eliminar`

Elimina definitivamente el documento:

- Intenta borrar el archivo físico en `uploads/`.
- Borra el registro `Documento`.
- Registra movimiento “eliminar”.
- Soporta `?next=/ruta` para volver a una URL interna.

### 8.6 Usuarios (solo Admin)

Pantalla: `templates/usuarios.html`

#### `GET /usuarios`

- Lista usuarios existentes.
- Permite seleccionar roles válidos: `Admin`, `Presidente`, `Vicepresidente`, `Asistente`.

#### `POST /usuarios/nuevo`

Crea usuario:

- Requiere `username`, `email`, `password`, `role`.
- Password mínimo: 6 caracteres.
- Username y email deben ser únicos.
- Registra movimiento “crear”.

#### `POST /usuarios/<id>/rol`

Actualiza rol:

- Valida que el rol esté en el set permitido.
- Evita que el admin actual se quite a sí mismo el rol Admin por accidente.
- Sincroniza `es_admin = (role == 'Admin')`.
- Registra movimiento “editar”.

#### `POST /usuarios/<id>/reset-password`

Resetea contraseña:

- Password mínimo: 6 caracteres.
- Registra movimiento “editar”.

### 8.7 Mi cuenta

Pantalla: `templates/mi_cuenta.html`

#### `GET|POST /mi-cuenta`

Permite cambiar tu propia contraseña:

- Verifica contraseña actual.
- Nueva contraseña mínimo 6 caracteres.
- Confirmación (repetir) debe coincidir.
- Registra movimiento “editar”.

### 8.8 Historial / auditoría (solo Admin)

Pantalla: `templates/registros.html`

#### `GET /registros`

Listado paginado (20 por página) de `RegistroMovimiento`, con filtros:

- `usuario`: puede ser id numérico o parte del nombre.
- `desde`: fecha `YYYY-MM-DD` (inicio del rango).
- `hasta`: fecha `YYYY-MM-DD` (fin del rango; se ajusta a 23:59:59.999999).

Se muestran movimientos ordenados por fecha descendente.

### 8.9 Validación de RUT (utilidad)

#### `GET|POST /validar-rut`

Página de prueba para validar un RUT manualmente y mostrar el resultado.

#### `POST /api/verificar-rut`

API JSON para validar y verificar disponibilidad:

- Entrada JSON:
  - `rut` (string)
  - `excluir_id` (opcional; para edición)
- Respuesta JSON:
  - `valido` (bool)
  - `mensaje` (string)

## 9) Archivos físicos y generación de PDFs

### 9.1 Carpeta de uploads

Todos los archivos subidos/generados se guardan en:

- `uploads/` (en la raíz de la app)

Nomenclatura:

- Documentos genéricos: `doc_<timestamp>_<archivo_sanitizado>`
- PDFs de certificados: `certhtml_<timestamp>_<archivo_sanitizado>.pdf`

### 9.2 Generación de PDF de certificado

Proceso:

- Se renderiza `certificado_plantilla.html` con los datos del certificado.
- Se intenta embebeder el logo como Base64 (si existe `static/junta de vecinos.jpg`).
- Playwright lanza Chromium headless y “imprime” a PDF:
  - Formato A4
  - `print_background=True`
  - márgenes 0

El PDF resultante se guarda en disco y se registra como `Documento` del tipo reservado.

## 10) Comportamientos/decisiones relevantes

- **Sin cache del navegador**: después de cada request se agregan headers `no-store/no-cache` para evitar ver páginas antiguas si cambian datos directamente en MySQL.
- **Soft vs hard delete**:
  - Vecinos, certificados y documentos: el flujo implementado elimina definitivamente (hard delete) en las rutas de eliminación.
  - Tipos de documento: se “eliminan” desactivando (`activo=False`) si no hay documentos asociados.
- **Consistencia del historial**:
  - La vista de historial oculta movimientos huérfanos para evitar inconsistencias si se manipula la DB manualmente.

## 11) Resumen rápido de endpoints (mapa)

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
- `GET /certificados`
- `GET|POST /certificados/nuevo`
- `GET|POST /certificados/<id>/editar`
- `GET /certificados/<id>/imprimir`
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

