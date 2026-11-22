from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import calendar
import csv
import io
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-123'

# --- НАСТРОЙКА БАЗЫ ДАННЫХ (SQLite локально, PostgreSQL на Render) ---
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    # Принудительно используем psycopg2 драйвер
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tracker.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = None
login_manager.login_message_category = 'warning'
login_manager.init_app(app)

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    habits = db.relationship('Habit', backref='owner', lazy=True)

class Habit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reminder_time = db.Column(db.String(5), nullable=True)
    is_archived = db.Column(db.Boolean, default=False)
    entries = db.relationship('Entry', backref='habit', lazy=True, cascade="all, delete-orphan")

class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    habit_id = db.Column(db.Integer, db.ForeignKey('habit.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    done = db.Column(db.Boolean, default=True)
    note = db.Column(db.String(255), nullable=True)
    mood = db.Column(db.String(20), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- МАРШРУТЫ ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Пользователь уже существует', 'danger')
            return redirect(url_for('register'))
        new_user = User(username=username, password_hash=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Неверное имя или пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.month, type=int)
    year = request.args.get('year', today.year, type=int)
    
    _, num_days = calendar.monthrange(year, month)
    days_in_month = [datetime(year, month, day).date() for day in range(1, num_days + 1)]
    
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    habits = Habit.query.filter_by(user_id=current_user.id, is_archived=False).all()
    
    for habit in habits:
        habit.days_status = []
        streak = 0
        check_date = today
        if not Entry.query.filter_by(habit_id=habit.id, date=today).first():
            check_date = today - timedelta(days=1)
            
        while True:
            entry = Entry.query.filter_by(habit_id=habit.id, date=check_date).first()
            if entry:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break
        habit.streak = streak

        for day in days_in_month:
            entry = Entry.query.filter_by(habit_id=habit.id, date=day).first()
            habit.days_status.append({
                'date': day,
                'done': entry is not None,
                'day_name': ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][day.weekday()],
                'note': entry.note if entry else None,
                'mood': entry.mood if entry else None
            })

    return render_template('dashboard.html', habits=habits, days_in_month=days_in_month,
                           current_month=month, current_year=year,
                           prev_month=prev_month, prev_year=prev_year,
                           next_month=next_month, next_year=next_year)

@app.route('/archive')
@login_required
def archive_page():
    archived_habits = Habit.query.filter_by(user_id=current_user.id, is_archived=True).all()
    return render_template('archive.html', habits=archived_habits)

@app.route('/stats')
@login_required
def stats():
    today = datetime.utcnow().date()
    last_30_days = [(today - timedelta(days=i)) for i in range(29, -1, -1)]
    habits = Habit.query.filter_by(user_id=current_user.id, is_archived=False).all()
    
    total_habits = len(habits)
    best_streak = 0
    total_done_days = 0
    total_possible_days = total_habits * 30 if total_habits > 0 else 0

    chart_labels = [day.strftime('%d.%m') for day in last_30_days]
    chart_data = []
    mood_data = {'happy': 0, 'neutral': 0, 'sad': 0}

    for day in last_30_days:
        done_count = 0
        for habit in habits:
            entry = Entry.query.filter_by(habit_id=habit.id, date=day).first()
            if entry:
                done_count += 1
                total_done_days += 1
                if entry.mood in mood_data: mood_data[entry.mood] += 1
        chart_data.append(done_count)

    for habit in habits:
        entries = Entry.query.filter_by(habit_id=habit.id).order_by(Entry.date.asc()).all()
        current_streak = 0
        last_date = None
        for entry in entries:
            if last_date is None: current_streak = 1
            elif (entry.date - last_date).days == 1: current_streak += 1
            else: current_streak = 1
            last_date = entry.date
            if current_streak > best_streak: best_streak = current_streak

    percent = round((total_done_days / total_possible_days * 100)) if total_possible_days > 0 else 0

    return render_template('stats.html', total_habits=total_habits, best_streak=best_streak, percent=percent,
                           chart_labels=chart_labels, chart_data=chart_data, mood_data=mood_data)

# --- ЭКСПОРТ ДАННЫХ ---
@app.route('/export')
@login_required
def export_data():
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    
    writer.writerow(['Привычка', 'Дата', 'Выполнено', 'Заметка', 'Настроение'])
    
    habits = Habit.query.filter_by(user_id=current_user.id).all()
    
    for habit in habits:
        for entry in habit.entries:
            writer.writerow([
                habit.name,
                entry.date.strftime('%Y-%m-%d'),
                'Да' if entry.done else 'Нет',
                entry.note or '',
                entry.mood or ''
            ])
            
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=my_habits.csv"}
    )

# --- НАСТРОЙКИ ---
@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/change_username', methods=['POST'])
@login_required
def change_username():
    new_username = request.form.get('new_username')
    if new_username and new_username != current_user.username:
        if not User.query.filter_by(username=new_username).first():
            current_user.username = new_username
            db.session.commit()
            flash('Никнейм изменен!', 'success')
        else:
            flash('Никнейм занят', 'danger')
    return redirect(url_for('settings'))

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    if not check_password_hash(current_user.password_hash, old_password):
        flash('Неверный текущий пароль', 'danger')
    elif len(new_password) < 4:
        flash('Пароль слишком короткий', 'warning')
    else:
        current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()
        flash('Пароль изменен!', 'success')
    return redirect(url_for('settings'))

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    user = User.query.get(current_user.id)
    logout_user()
    db.session.delete(user)
    db.session.commit()
    flash('Аккаунт удален.', 'info')
    return redirect(url_for('login'))

# --- ДЕЙСТВИЯ С ПРИВЫЧКАМИ ---
@app.route('/add_habit', methods=['POST'])
@login_required
def add_habit():
    name = request.form.get('name')
    reminder_time = request.form.get('reminder_time')
    if name:
        new_habit = Habit(name=name, reminder_time=reminder_time, user_id=current_user.id)
        db.session.add(new_habit)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/set_reminder/<int:habit_id>', methods=['POST'])
@login_required
def set_reminder(habit_id):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        habit.reminder_time = request.form.get('reminder_time')
        db.session.commit()
        flash(f'Время напоминания обновлено!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/edit_habit/<int:habit_id>', methods=['POST'])
@login_required
def edit_habit(habit_id):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        habit.name = request.form.get('new_name')
        db.session.commit()
        flash('Привычка обновлена', 'success')
    return redirect(url_for('dashboard'))

@app.route('/archive_habit/<int:habit_id>', methods=['POST'])
@login_required
def archive_habit(habit_id):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        habit.is_archived = True
        db.session.commit()
        flash(f'Привычка отправлена в архив.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/unarchive_habit/<int:habit_id>', methods=['POST'])
@login_required
def unarchive_habit(habit_id):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        habit.is_archived = False
        db.session.commit()
        flash(f'Привычка возвращена.', 'success')
    return redirect(url_for('archive_page'))

@app.route('/delete_habit/<int:habit_id>', methods=['POST'])
@login_required
def delete_habit(habit_id):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        db.session.delete(habit)
        db.session.commit()
        flash('Привычка удалена', 'success')
    return redirect(url_for('dashboard'))

@app.route('/toggle_habit_date/<int:habit_id>/<date_str>', methods=['POST'])
@login_required
def toggle_habit_date(habit_id, date_str):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        entry = Entry.query.filter_by(habit_id=habit.id, date=target_date).first()
        if entry:
            db.session.delete(entry)
        else:
            new_entry = Entry(habit_id=habit.id, date=target_date)
            db.session.add(new_entry)
        db.session.commit()
    return redirect(url_for('dashboard', month=request.args.get('month'), year=request.args.get('year')))

@app.route('/add_note/<int:habit_id>/<date_str>', methods=['POST'])
@login_required
def add_note(habit_id, date_str):
    habit = Habit.query.get_or_404(habit_id)
    if habit.user_id == current_user.id:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        note_text = request.form.get('note')
        mood = request.form.get('mood')
        
        entry = Entry.query.filter_by(habit_id=habit.id, date=target_date).first()
        if entry:
            entry.note = note_text
            entry.mood = mood
            if note_text or mood: entry.done = True
        else:
            new_entry = Entry(habit_id=habit.id, date=target_date, note=note_text, mood=mood, done=True)
            db.session.add(new_entry)
        db.session.commit()
    return redirect(url_for('dashboard', month=request.args.get('month'), year=request.args.get('year')))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)