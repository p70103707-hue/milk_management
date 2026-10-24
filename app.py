import os
from flask import Flask, redirect, url_for, session
from config import Config
from models.models import db
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.milkman import milkman_bp
from routes.customer import customer_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Database
    db.init_app(app)

    # Ensure upload/backup directories exist
    os.makedirs(os.path.join(app.root_path, 'uploads', 'backups'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'database'), exist_ok=True)

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(milkman_bp)
    app.register_blueprint(customer_bp)

    @app.route('/')
    def index():
        if 'user_id' in session:
            role = session.get('role')
            if role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif role == 'milkman':
                return redirect(url_for('milkman.dashboard'))
            elif role == 'customer':
                return redirect(url_for('customer.dashboard'))
        return redirect(url_for('auth.login'))

    @app.after_request
    def add_header(response):
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type or 'application/json' in content_type:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '-1'
        return response

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
