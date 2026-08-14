#!/usr/bin/env python3
"""
Script de configuración inicial para el Sistema de Gestión de Vecinos
"""

import os
import sys
import subprocess
import sqlite3
from pathlib import Path

def print_banner():
    """Imprime el banner del proyecto"""
    print("""
    🏘️  Sistema de Gestión de Vecinos
    ====================================
    Configuración inicial del proyecto
    """)

def check_python_version():
    """Verifica la versión de Python"""
    print("✓ Verificando versión de Python...")
    if sys.version_info < (3, 8):
        print("❌ Error: Se requiere Python 3.8 o superior")
        sys.exit(1)
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor} detectado")

def create_env_file():
    """Crea el archivo .env si no existe"""
    print("✓ Configurando variables de entorno...")
    env_file = Path(".env")
    if not env_file.exists():
        with open(env_file, "w") as f:
            f.write("SECRET_KEY=tu-clave-secreta-super-segura-aqui-cambiala-en-produccion\n")
            f.write("FLASK_ENV=development\n")
            f.write("FLASK_DEBUG=1\n")
        print("✓ Archivo .env creado")
    else:
        print("✓ Archivo .env ya existe")

def install_dependencies():
    """Instala las dependencias del proyecto"""
    print("✓ Instalando dependencias...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✓ Dependencias instaladas correctamente")
    except subprocess.CalledProcessError:
        print("❌ Error al instalar dependencias")
        sys.exit(1)

def create_database():
    """Crea la base de datos SQLite para desarrollo"""
    print("✓ Configurando base de datos...")
    try:
        # Importar después de instalar dependencias
        from app import app, db
        
        with app.app_context():
            db.create_all()
            print("✓ Base de datos creada correctamente")
            
            # Crear usuario admin si no existe
            from app import Usuario
            if not Usuario.query.filter_by(username='admin').first():
                admin = Usuario(username='admin', email='admin@junta.com', es_admin=True)
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print("✓ Usuario administrador creado")
            
    except Exception as e:
        print(f"❌ Error al crear la base de datos: {e}")
        print("💡 Asegúrate de que XAMPP esté ejecutándose y la base de datos 'junta_vecinos' exista")

def main():
    """Función principal del script"""
    print_banner()
    
    try:
        check_python_version()
        create_env_file()
        install_dependencies()
        create_database()
        
        print("""
    ✅ Configuración completada exitosamente!
    
    🚀 Para ejecutar la aplicación:
        python app.py
    
    🌐 La aplicación estará disponible en:
        http://localhost:5000
    
    🔐 Credenciales por defecto:
        Usuario: admin
        Contraseña: admin123
        
    📚 Consulta el README.md para más información
        """)
        
    except KeyboardInterrupt:
        print("\n❌ Configuración cancelada por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error durante la configuración: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 