"""JSON APIs under /api/, one blueprint split across submodules."""
from flask import Blueprint

bp = Blueprint('api', __name__)

# Imported for their side effect of registering routes on `bp`.
from pet_platform.blueprints.api import accounts, chat, orders, reviews  # noqa: E402,F401
