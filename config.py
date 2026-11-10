"""App settings. Paths are absolute so the app works no matter which folder it is started from."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# A fixed development key keeps everyone logged in across restarts.
# Set the SECRET_KEY environment variable to override it.
SECRET_KEY = os.environ.get('SECRET_KEY', 'pet-platform-dev-secret-key')

DATABASE = os.environ.get('DATABASE', os.path.join(BASE_DIR, 'database.db'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
