"""Pet companion listing platform."""
import os

from flask import Flask

from pet_platform import db
from pet_platform.utils.time import utc8_filter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app(test_config=None):
    # templates/ and static/ stay at the project root.
    app = Flask(__name__,
                template_folder=os.path.join(PROJECT_ROOT, 'templates'),
                static_folder=os.path.join(PROJECT_ROOT, 'static'))
    app.config.from_pyfile(os.path.join(PROJECT_ROOT, 'config.py'))
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    app.add_template_filter(utc8_filter, 'utc8')

    from pet_platform.context_processors import inject_sidebar_counts
    app.context_processor(inject_sidebar_counts)

    from pet_platform.blueprints import admin, auth, main, member, trainer
    from pet_platform.blueprints.api import bp as api_bp
    for bp in (auth.bp, main.bp, member.bp, trainer.bp, admin.bp, api_bp):
        app.register_blueprint(bp)

    return app
