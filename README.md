# 🏘️ Sistema de Gestión de Vecinos

Sistema web para la administración y gestión de información de los vecinos de un cuadrante, desarrollado con Python, Flask, MySQL (XAMPP) y Tailwind CSS.

## 🚀 Características

- **Sistema de Autenticación**: Login seguro para acceder a la información
- **Gestión de Vecinos**: CRUD completo para vecinos (Crear, Leer, Actualizar, Eliminar)
- **Interfaz Moderna**: Diseño responsive con Tailwind CSS
- **Base de Datos MySQL**: Almacenamiento seguro con XAMPP
- **Dashboard Intuitivo**: Vista organizada de todos los vecinos registrados

## 📋 Requisitos Previos

- Python 3.8 o superior
- XAMPP (para MySQL)
- pip (gestor de paquetes de Python)

## 🛠️ Instalación

### 1. Clonar o descargar el proyecto
```bash
git clone <url-del-repositorio>
cd JuntaDeVecinos
```

### 2. Crear entorno virtual (recomendado)
```bash
python -m venv venv
# En Windows:
venv\Scripts\activate
# En macOS/Linux:
source venv/bin/activate
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar XAMPP
1. Iniciar XAMPP Control Panel
2. Iniciar Apache y MySQL
3. Abrir phpMyAdmin (http://localhost/phpmyadmin)
4. Crear una nueva base de datos llamada `junta_vecinos`

### 5. Configurar variables de entorno
Renombrar `config.env` a `.env` y modificar las variables según sea necesario:
```env
SECRET_KEY=tu-clave-secreta-super-segura-aqui-cambiala-en-produccion
FLASK_ENV=development
FLASK_DEBUG=1
```

### 6. Ejecutar la aplicación
```bash
python app.py
```

La aplicación estará disponible en: http://localhost:5000

## 🔐 Credenciales por Defecto

- **Usuario**: admin
- **Contraseña**: admin123

**⚠️ Importante**: Cambia estas credenciales después del primer inicio de sesión.

## 📁 Estructura del Proyecto

```
JuntaDeVecinos/
├── app.py                 # Aplicación principal Flask
├── requirements.txt       # Dependencias de Python
├── config.env            # Variables de entorno
├── README.md             # Documentación
└── templates/            # Plantillas HTML
    ├── base.html         # Plantilla base
    ├── index.html        # Página principal
    ├── login.html        # Página de login
    ├── dashboard.html    # Dashboard principal
    ├── nuevo_vecino.html # Formulario nuevo vecino
    └── editar_vecino.html # Formulario editar vecino
```

## 🗄️ Base de Datos

### Tablas

#### Usuario
- `id` (INT, PK)
- `username` (VARCHAR(80), UNIQUE)
- `email` (VARCHAR(120), UNIQUE)
- `password_hash` (VARCHAR(255))
- `es_admin` (BOOLEAN)

#### Vecino
- `id` (INT, PK)
- `nombre` (VARCHAR(100))
- `apellidos` (VARCHAR(100))
- `telefono` (VARCHAR(20))
- `domicilio` (VARCHAR(200))
- `rut` (VARCHAR(20), UNIQUE)
- `fecha_registro` (DATETIME)
- `notas` (TEXT)

## 🎨 Tecnologías Utilizadas

- **Backend**: Python, Flask, SQLAlchemy
- **Base de Datos**: MySQL (XAMPP)
- **Frontend**: HTML, Tailwind CSS
- **Autenticación**: Flask-Login
- **Formularios**: Flask-WTF

## 🔧 Funcionalidades

### Gestión de Usuarios
- Sistema de login/logout
- Protección de rutas con autenticación
- Usuario administrador por defecto

### Gestión de Vecinos
- **Agregar**: Formulario completo para nuevos vecinos
- **Ver**: Dashboard con tabla de todos los vecinos
- **Editar**: Modificar información existente
- **Eliminar**: Borrar vecinos con confirmación

### Interfaz de Usuario
- Diseño responsive
- Mensajes de confirmación
- Validación de formularios
- Navegación intuitiva

## 🚀 Uso

1. **Acceder al sistema**: Ve a http://localhost:5000
2. **Iniciar sesión**: Usa las credenciales por defecto
3. **Gestionar vecinos**: Desde el dashboard puedes agregar, editar o eliminar vecinos
4. **Navegar**: Usa la barra de navegación para moverte entre secciones

## 🔒 Seguridad

- Contraseñas hasheadas con Werkzeug
- Sesiones seguras con Flask-Login
- Protección CSRF implícita
- Validación de formularios

## 🐛 Solución de Problemas

### Error de conexión a MySQL
- Verifica que XAMPP esté ejecutándose
- Confirma que la base de datos `junta_vecinos` existe
- Revisa las credenciales en `app.py`

### Error de módulos no encontrados
- Asegúrate de tener el entorno virtual activado
- Ejecuta `pip install -r requirements.txt`

### Error de puerto ocupado
- Cambia el puerto en `app.py` línea final
- O termina el proceso que usa el puerto 5000

## 📝 Licencia

Este proyecto es de uso libre para fines educativos y comunitarios.

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Por favor, abre un issue o pull request para sugerencias y mejoras.

---

**Desarrollado con ❤️ para la gestión comunitaria** 