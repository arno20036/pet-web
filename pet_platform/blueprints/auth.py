"""Register / login / logout."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from pet_platform.db import get_db
from pet_platform.utils.time import NOW_SQL

bp = Blueprint('auth', __name__)


@bp.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        name = request.form['name']
        nickname = request.form['nickname']
        gender = request.form['gender']
        dob = request.form['dob']
        city = request.form['city']
        district = request.form['district']
        phone = request.form['phone']

        if password != confirm_password:
            flash('兩次輸入的密碼不一致', 'error')
            return redirect(url_for('auth.register'))

        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user:
            flash('該電子信箱已被註冊', 'error')
            return redirect(url_for('auth.register'))

        password_hash = generate_password_hash(password)

        try:
            conn.execute(f'INSERT INTO users (email, password_hash, name, nickname, gender, dob, city, district, phone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {NOW_SQL})',
                         (email, password_hash, name, nickname, gender, dob, city, district, phone))
            conn.commit()
            flash('註冊成功，請登入', 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            flash(f'註冊失敗: {str(e)}', 'error')

    return render_template('register.html')

@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user is None:
            flash('帳號不存在', 'error')
        elif not check_password_hash(user['password_hash'], password):
            flash('密碼錯誤', 'error')
        else:
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['nickname']
            session['user_role'] = user['role']
            flash('登入成功', 'success')
            return redirect(url_for('main.index'))

    return render_template('login.html')

@bp.route('/logout')
def logout():
    session.clear()
    flash('您已登出', 'success')
    return redirect(url_for('auth.login'))
