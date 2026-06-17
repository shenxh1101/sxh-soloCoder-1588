import os
import random
import csv
import io
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cold-monitor-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'cold_storage.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class ColdStorage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    temp_min = db.Column(db.Float, nullable=False)
    temp_max = db.Column(db.Float, nullable=False)
    humidity_min = db.Column(db.Float, nullable=False)
    humidity_max = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    records = db.relationship('TemperatureRecord', backref='storage', lazy=True, cascade='all, delete-orphan')
    alarms = db.relationship('AlarmLog', backref='storage', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'temp_min': self.temp_min,
            'temp_max': self.temp_max,
            'humidity_min': self.humidity_min,
            'humidity_max': self.humidity_max,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }


class ImportBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    storage_id = db.Column(db.Integer, db.ForeignKey('cold_storage.id'), nullable=False)
    batch_type = db.Column(db.String(20), default='manual')
    record_count = db.Column(db.Integer, default=0)
    alarm_count = db.Column(db.Integer, default=0)
    imported_at = db.Column(db.DateTime, default=datetime.now)
    imported_by = db.Column(db.String(100), default='系统导入')
    note = db.Column(db.String(500))
    is_revoked = db.Column(db.Boolean, default=False)
    revoked_at = db.Column(db.DateTime)

    records = db.relationship('TemperatureRecord', backref='batch', lazy='dynamic',
                              foreign_keys='TemperatureRecord.batch_id')
    alarms = db.relationship('AlarmLog', backref='batch', lazy='dynamic',
                             foreign_keys='AlarmLog.batch_id')

    def to_dict(self):
        return {
            'id': self.id,
            'storage_id': self.storage_id,
            'storage_name': self.storage.name if self.storage else '未知',
            'batch_type': self.batch_type,
            'record_count': self.record_count,
            'alarm_count': self.alarm_count,
            'imported_at': self.imported_at.strftime('%Y-%m-%d %H:%M:%S'),
            'imported_by': self.imported_by,
            'note': self.note,
            'is_revoked': self.is_revoked,
            'revoked_at': self.revoked_at.strftime('%Y-%m-%d %H:%M:%S') if self.revoked_at else None
        }


class TemperatureRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    storage_id = db.Column(db.Integer, db.ForeignKey('cold_storage.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('import_batch.id'), nullable=True)
    temperature = db.Column(db.Float, nullable=False)
    humidity = db.Column(db.Float, nullable=False)
    is_manual = db.Column(db.Boolean, default=False)
    recorded_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'storage_id': self.storage_id,
            'batch_id': self.batch_id,
            'temperature': self.temperature,
            'humidity': self.humidity,
            'is_manual': self.is_manual,
            'recorded_at': self.recorded_at.strftime('%Y-%m-%d %H:%M:%S')
        }


class AlarmLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    storage_id = db.Column(db.Integer, db.ForeignKey('cold_storage.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('import_batch.id'), nullable=True)
    alarm_type = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(200), nullable=False)
    current_value = db.Column(db.Float, nullable=False)
    threshold_min = db.Column(db.Float)
    threshold_max = db.Column(db.Float)
    triggered_at = db.Column(db.DateTime, default=datetime.now)
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.String(100))
    handled_note = db.Column(db.String(500))
    is_handled = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id': self.id,
            'storage_id': self.storage_id,
            'storage_name': self.storage.name,
            'batch_id': self.batch_id,
            'alarm_type': self.alarm_type,
            'message': self.message,
            'current_value': self.current_value,
            'threshold_min': self.threshold_min,
            'threshold_max': self.threshold_max,
            'triggered_at': self.triggered_at.strftime('%Y-%m-%d %H:%M:%S'),
            'handled_at': self.handled_at.strftime('%Y-%m-%d %H:%M:%S') if self.handled_at else None,
            'handled_by': self.handled_by,
            'handled_note': self.handled_note,
            'is_handled': self.is_handled
        }


class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alarm_id = db.Column(db.Integer, db.ForeignKey('alarm_log.id'), nullable=False)
    recipient = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'alarm_id': self.alarm_id,
            'recipient': self.recipient,
            'subject': self.subject,
            'content': self.content,
            'sent_at': self.sent_at.strftime('%Y-%m-%d %H:%M:%S')
        }


def generate_sensor_data(storage):
    temp_mid = (storage.temp_min + storage.temp_max) / 2
    temp_range = (storage.temp_max - storage.temp_min) / 4
    humidity_mid = (storage.humidity_min + storage.humidity_max) / 2
    humidity_range = (storage.humidity_max - storage.humidity_min) / 4

    if random.random() < 0.15:
        temperature = random.uniform(storage.temp_min - 5, storage.temp_max + 5)
    else:
        temperature = random.uniform(temp_mid - temp_range, temp_mid + temp_range)

    if random.random() < 0.15:
        humidity = random.uniform(max(0, storage.humidity_min - 15), min(100, storage.humidity_max + 15))
    else:
        humidity = random.uniform(humidity_mid - humidity_range, humidity_mid + humidity_range)

    return round(temperature, 1), round(humidity, 1)


def check_alarm(storage, temperature, humidity, record_time=None):
    alarms = []
    if record_time is None:
        record_time = datetime.now()

    if temperature < storage.temp_min:
        alarm = AlarmLog(
            storage_id=storage.id,
            alarm_type='temp_low',
            message=f'温度过低：当前{temperature}°C，低于设定下限{storage.temp_min}°C',
            current_value=temperature,
            threshold_min=storage.temp_min,
            threshold_max=storage.temp_max,
            triggered_at=record_time
        )
        alarms.append(alarm)
    elif temperature > storage.temp_max:
        alarm = AlarmLog(
            storage_id=storage.id,
            alarm_type='temp_high',
            message=f'温度过高：当前{temperature}°C，高于设定上限{storage.temp_max}°C',
            current_value=temperature,
            threshold_min=storage.temp_min,
            threshold_max=storage.temp_max,
            triggered_at=record_time
        )
        alarms.append(alarm)

    if humidity < storage.humidity_min:
        alarm = AlarmLog(
            storage_id=storage.id,
            alarm_type='humidity_low',
            message=f'湿度过低：当前{humidity}%，低于设定下限{storage.humidity_min}%',
            current_value=humidity,
            threshold_min=storage.humidity_min,
            threshold_max=storage.humidity_max,
            triggered_at=record_time
        )
        alarms.append(alarm)
    elif humidity > storage.humidity_max:
        alarm = AlarmLog(
            storage_id=storage.id,
            alarm_type='humidity_high',
            message=f'湿度过高：当前{humidity}%，高于设定上限{storage.humidity_max}%',
            current_value=humidity,
            threshold_min=storage.humidity_min,
            threshold_max=storage.humidity_max,
            triggered_at=record_time
        )
        alarms.append(alarm)

    return alarms


def send_simulated_email(alarm):
    recipient = 'admin@coldstorage.com'
    storage = ColdStorage.query.get(alarm.storage_id)
    subject = f'【报警】冷库{storage.name} - {alarm.message[:50]}'
    content = f"""
    冷库温湿度监控系统报警通知
    ========================
    冷库名称：{storage.name}
    报警时间：{alarm.triggered_at.strftime('%Y-%m-%d %H:%M:%S')}
    报警类型：{'温度' if 'temp' in alarm.alarm_type else '湿度'}报警
    当前数值：{alarm.current_value}
    设定范围：{alarm.threshold_min} ~ {alarm.threshold_max}
    报警内容：{alarm.message}
    
    请及时处理！
    """

    email_log = EmailLog(
        alarm_id=alarm.id,
        recipient=recipient,
        subject=subject,
        content=content
    )
    db.session.add(email_log)
    db.session.commit()

    print(f"[模拟邮件] 已发送到 {recipient}")
    print(f"  主题: {subject}")
    return email_log


def auto_record_data():
    with app.app_context():
        storages = ColdStorage.query.all()
        for storage in storages:
            temperature, humidity = generate_sensor_data(storage)

            record = TemperatureRecord(
                storage_id=storage.id,
                temperature=temperature,
                humidity=humidity,
                is_manual=False
            )
            db.session.add(record)

            alarms = check_alarm(storage, temperature, humidity)
            for alarm in alarms:
                db.session.add(alarm)
                db.session.flush()
                send_simulated_email(alarm)

        db.session.commit()
        print(f"[定时任务] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 完成自动数据采集")


@app.route('/')
def index():
    storages = ColdStorage.query.all()
    active_alarms = AlarmLog.query.filter_by(is_handled=False).order_by(AlarmLog.triggered_at.desc()).all()
    return render_template('index.html', storages=storages, active_alarms=active_alarms)


@app.route('/storages', methods=['GET', 'POST'])
def storages():
    if request.method == 'POST':
        data = request.json if request.is_json else request.form
        storage = ColdStorage(
            name=data.get('name'),
            temp_min=float(data.get('temp_min')),
            temp_max=float(data.get('temp_max')),
            humidity_min=float(data.get('humidity_min')),
            humidity_max=float(data.get('humidity_max'))
        )
        db.session.add(storage)
        db.session.commit()
        flash(f'冷库 {storage.name} 创建成功！', 'success')
        if request.is_json:
            return jsonify({'status': 'success', 'storage': storage.to_dict()})
        return redirect(url_for('storages'))

    storages = ColdStorage.query.all()
    return render_template('storages.html', storages=storages)


@app.route('/storages/<int:storage_id>', methods=['GET', 'PUT', 'DELETE'])
def storage_detail(storage_id):
    storage = ColdStorage.query.get_or_404(storage_id)

    if request.method == 'PUT':
        data = request.json
        storage.name = data.get('name', storage.name)
        storage.temp_min = float(data.get('temp_min', storage.temp_min))
        storage.temp_max = float(data.get('temp_max', storage.temp_max))
        storage.humidity_min = float(data.get('humidity_min', storage.humidity_min))
        storage.humidity_max = float(data.get('humidity_max', storage.humidity_max))
        db.session.commit()
        return jsonify({'status': 'success', 'storage': storage.to_dict()})

    if request.method == 'DELETE':
        db.session.delete(storage)
        db.session.commit()
        return jsonify({'status': 'success'})

    hours_ago = request.args.get('hours', 24, type=int)
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours_ago)

    records = TemperatureRecord.query.filter(
        TemperatureRecord.storage_id == storage_id,
        TemperatureRecord.recorded_at >= start_time,
        TemperatureRecord.recorded_at <= end_time
    ).order_by(TemperatureRecord.recorded_at).all()

    return jsonify({
        'storage': storage.to_dict(),
        'records': [r.to_dict() for r in records]
    })


@app.route('/charts')
def charts():
    storages = ColdStorage.query.all()
    return render_template('charts.html', storages=storages)


@app.route('/api/chart-data/<int:storage_id>')
def chart_data(storage_id):
    storage = ColdStorage.query.get_or_404(storage_id)
    hours = request.args.get('hours', 24, type=int)
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    records = TemperatureRecord.query.filter(
        TemperatureRecord.storage_id == storage_id,
        TemperatureRecord.recorded_at >= start_time
    ).order_by(TemperatureRecord.recorded_at).all()

    data = {
        'labels': [],
        'temperature': [],
        'humidity': [],
        'temp_min': storage.temp_min,
        'temp_max': storage.temp_max,
        'humidity_min': storage.humidity_min,
        'humidity_max': storage.humidity_max
    }

    for r in records:
        data['labels'].append(r.recorded_at.strftime('%Y-%m-%d %H:%M'))
        data['temperature'].append(r.temperature)
        data['humidity'].append(r.humidity)

    return jsonify(data)


@app.route('/manual-entry', methods=['GET', 'POST'])
def manual_entry():
    if request.method == 'POST':
        data = request.json if request.is_json else request.form
        storage_id = int(data.get('storage_id'))
        temperature = float(data.get('temperature'))
        humidity = float(data.get('humidity'))
        record_time_str = data.get('recorded_at')

        if record_time_str:
            try:
                record_time = datetime.strptime(record_time_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                record_time = datetime.strptime(record_time_str, '%Y-%m-%d %H:%M:%S')
        else:
            record_time = datetime.now()

        storage = ColdStorage.query.get_or_404(storage_id)

        record = TemperatureRecord(
            storage_id=storage_id,
            temperature=temperature,
            humidity=humidity,
            is_manual=True,
            recorded_at=record_time
        )
        db.session.add(record)

        alarms = check_alarm(storage, temperature, humidity, record_time)
        for alarm in alarms:
            db.session.add(alarm)
            db.session.flush()
            send_simulated_email(alarm)

        db.session.commit()
        flash('手动录入数据成功！', 'success')
        if request.is_json:
            return jsonify({'status': 'success', 'record': record.to_dict()})
        return redirect(url_for('manual_entry'))

    storages = ColdStorage.query.all()
    return render_template('manual_entry.html', storages=storages)


@app.route('/alarms')
def alarms():
    storages = ColdStorage.query.all()
    return render_template('alarms.html', storages=storages)


@app.route('/alarms/<int:alarm_id>/handle', methods=['POST'])
def handle_alarm(alarm_id):
    alarm = AlarmLog.query.get_or_404(alarm_id)
    data = request.json if request.is_json else request.form

    alarm.handled_by = data.get('handled_by')
    alarm.handled_note = data.get('handled_note')
    alarm.handled_at = datetime.now()
    alarm.is_handled = True

    db.session.commit()
    return jsonify({'status': 'success', 'alarm': alarm.to_dict()})


@app.route('/reports')
def reports():
    storages = ColdStorage.query.all()
    return render_template('reports.html', storages=storages)


@app.route('/api/report')
def generate_report():
    storage_id = request.args.get('storage_id', type=int)
    report_type = request.args.get('type', 'daily')

    if report_type == 'daily':
        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)
    elif report_type == 'weekly':
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
    else:
        start_str = request.args.get('start_date')
        end_str = request.args.get('end_date')
        start_time = datetime.strptime(start_str, '%Y-%m-%d') if start_str else datetime.now() - timedelta(days=1)
        end_time = datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=1) if end_str else datetime.now()

    query = TemperatureRecord.query.filter(
        TemperatureRecord.recorded_at >= start_time,
        TemperatureRecord.recorded_at < end_time
    )
    if storage_id:
        query = query.filter_by(storage_id=storage_id)

    records = query.order_by(TemperatureRecord.recorded_at).all()

    export_start = start_time.strftime('%Y-%m-%d %H:%M:%S')
    export_end = end_time.strftime('%Y-%m-%d %H:%M:%S')

    if not records:
        return jsonify({
            'storage_id': storage_id,
            'report_type': report_type,
            'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'export_start': export_start,
            'export_end': export_end,
            'summary': None,
            'daily_data': []
        })

    temp_values = [r.temperature for r in records]
    humidity_values = [r.humidity for r in records]

    summary = {
        'total_records': len(records),
        'temperature': {
            'avg': round(sum(temp_values) / len(temp_values), 2),
            'max': max(temp_values),
            'min': min(temp_values)
        },
        'humidity': {
            'avg': round(sum(humidity_values) / len(humidity_values), 2),
            'max': max(humidity_values),
            'min': min(humidity_values)
        }
    }

    daily_data = defaultdict(lambda: {'temps': [], 'humidities': []})
    for r in records:
        day_key = r.recorded_at.strftime('%Y-%m-%d')
        daily_data[day_key]['temps'].append(r.temperature)
        daily_data[day_key]['humidities'].append(r.humidity)

    daily_stats = []
    for day in sorted(daily_data.keys()):
        temps = daily_data[day]['temps']
        hums = daily_data[day]['humidities']
        daily_stats.append({
            'date': day,
            'temperature': {
                'avg': round(sum(temps) / len(temps), 2),
                'max': max(temps),
                'min': min(temps)
            },
            'humidity': {
                'avg': round(sum(hums) / len(hums), 2),
                'max': max(hums),
                'min': min(hums)
            },
            'record_count': len(temps)
        })

    alarm_count = AlarmLog.query.filter(
        AlarmLog.triggered_at >= start_time,
        AlarmLog.triggered_at < end_time
    )
    if storage_id:
        alarm_count = alarm_count.filter_by(storage_id=storage_id)
    summary['alarm_count'] = alarm_count.count()

    return jsonify({
        'storage_id': storage_id,
        'storage_name': ColdStorage.query.get(storage_id).name if storage_id else '全部冷库',
        'report_type': report_type,
        'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
        'export_start': export_start,
        'export_end': export_end,
        'summary': summary,
        'daily_data': daily_stats
    })


def _parse_datetime_param(param_str, default):
    if not param_str:
        return default
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(param_str, fmt)
            if fmt == '%Y-%m-%d':
                dt = dt + timedelta(days=1) if 'end' in param_str.lower() or 'end' in str(request.args) else dt
            return dt
        except ValueError:
            continue
    return default


@app.route('/export/csv')
def export_csv():
    storage_id = request.args.get('storage_id', type=int)
    start_str = request.args.get('start') or request.args.get('start_date')
    end_str = request.args.get('end') or request.args.get('end_date')

    start_time = _parse_datetime_param(start_str, datetime.now() - timedelta(days=7))
    end_time = _parse_datetime_param(end_str, datetime.now())

    query = TemperatureRecord.query.filter(
        TemperatureRecord.recorded_at >= start_time,
        TemperatureRecord.recorded_at < end_time
    )
    if storage_id:
        query = query.filter_by(storage_id=storage_id)

    records = query.order_by(TemperatureRecord.recorded_at).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', '冷库名称', '温度(°C)', '湿度(%)', '是否手动录入', '记录时间'])

    for r in records:
        storage = ColdStorage.query.get(r.storage_id)
        writer.writerow([
            r.id,
            storage.name if storage else '未知',
            r.temperature,
            r.humidity,
            '是' if r.is_manual else '否',
            r.recorded_at.strftime('%Y-%m-%d %H:%M:%S')
        ])

    output.seek(0)
    filename = f"temperature_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/api/report/compare')
def report_compare():
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    report_type = request.args.get('type', 'daily')

    if report_type == 'daily':
        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)
    elif report_type == 'weekly':
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
    else:
        start_time = _parse_datetime_param(start_str, datetime.now() - timedelta(days=1))
        end_time = _parse_datetime_param(end_str, datetime.now())

    storages = ColdStorage.query.all()
    results = []

    for storage in storages:
        records = TemperatureRecord.query.filter(
            TemperatureRecord.storage_id == storage.id,
            TemperatureRecord.recorded_at >= start_time,
            TemperatureRecord.recorded_at < end_time
        ).all()

        alarms = AlarmLog.query.filter(
            AlarmLog.storage_id == storage.id,
            AlarmLog.triggered_at >= start_time,
            AlarmLog.triggered_at < end_time
        ).all()

        if records:
            temp_values = [r.temperature for r in records]
            humidity_values = [r.humidity for r in records]
            temp_avg = sum(temp_values) / len(temp_values)
            humidity_avg = sum(humidity_values) / len(humidity_values)

            temp_violations = sum(1 for t in temp_values if t < storage.temp_min or t > storage.temp_max)
            humidity_violations = sum(1 for h in humidity_values if h < storage.humidity_min or h > storage.humidity_max)

            temp_std = (sum((t - temp_avg) ** 2 for t in temp_values) / len(temp_values)) ** 0.5
            humidity_std = (sum((h - humidity_avg) ** 2 for h in humidity_values) / len(humidity_values)) ** 0.5
            instability_score = round((temp_std + humidity_std) * 10 + len(alarms) * 5, 2)
        else:
            temp_avg = humidity_avg = 0
            temp_values = humidity_values = []
            temp_violations = humidity_violations = 0
            instability_score = 0

        results.append({
            'storage_id': storage.id,
            'storage_name': storage.name,
            'temp_range': f'{storage.temp_min} ~ {storage.temp_max}°C',
            'humidity_range': f'{storage.humidity_min} ~ {storage.humidity_max}%',
            'total_records': len(records),
            'temp_avg': round(temp_avg, 2),
            'temp_max': max(temp_values) if temp_values else None,
            'temp_min': min(temp_values) if temp_values else None,
            'humidity_avg': round(humidity_avg, 2),
            'humidity_max': max(humidity_values) if humidity_values else None,
            'humidity_min': min(humidity_values) if humidity_values else None,
            'temp_violations': temp_violations,
            'humidity_violations': humidity_violations,
            'alarm_count': len(alarms),
            'instability_score': instability_score
        })

    results.sort(key=lambda x: x['instability_score'], reverse=True)

    for i, r in enumerate(results):
        r['rank'] = i + 1

    total_seconds = (end_time - start_time).total_seconds()
    max_points = 24
    if total_seconds <= 86400:
        interval_seconds = 3600
    elif total_seconds <= 86400 * 3:
        interval_seconds = 3600 * 6
    elif total_seconds <= 86400 * 7:
        interval_seconds = 86400
    else:
        interval_seconds = 86400 * 3

    num_intervals = min(max_points, int(total_seconds / interval_seconds) + 1)
    actual_interval = total_seconds / (num_intervals - 1) if num_intervals > 1 else total_seconds

    labels = []
    for i in range(num_intervals):
        t = start_time + timedelta(seconds=actual_interval * i)
        if actual_interval >= 86400:
            labels.append(t.strftime('%m-%d'))
        else:
            labels.append(t.strftime('%m-%d %H:%M'))

    temp_datasets = []
    humidity_datasets = []
    colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

    for idx, storage in enumerate(storages):
        color = colors[idx % len(colors)]
        records = TemperatureRecord.query.filter(
            TemperatureRecord.storage_id == storage.id,
            TemperatureRecord.recorded_at >= start_time,
            TemperatureRecord.recorded_at < end_time
        ).order_by(TemperatureRecord.recorded_at.asc()).all()

        temp_values = [None] * num_intervals
        humidity_values = [None] * num_intervals

        for i in range(num_intervals):
            bucket_start = start_time + timedelta(seconds=actual_interval * i)
            bucket_end = start_time + timedelta(seconds=actual_interval * (i + 1))

            bucket_records = [
                r for r in records
                if bucket_start <= r.recorded_at < bucket_end
            ]

            if bucket_records:
                temp_values[i] = round(sum(r.temperature for r in bucket_records) / len(bucket_records), 2)
                humidity_values[i] = round(sum(r.humidity for r in bucket_records) / len(bucket_records), 2)

        temp_datasets.append({
            'storage_id': storage.id,
            'storage_name': storage.name,
            'color': color,
            'data': temp_values
        })
        humidity_datasets.append({
            'storage_id': storage.id,
            'storage_name': storage.name,
            'color': color,
            'data': humidity_values
        })

    return jsonify({
        'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
        'storages': results,
        'trend': {
            'labels': labels,
            'temperature_datasets': temp_datasets,
            'humidity_datasets': humidity_datasets
        }
    })


@app.route('/api/alarms')
def api_alarms_list():
    storage_id = request.args.get('storage_id', type=int)
    is_handled = request.args.get('is_handled')
    alarm_type = request.args.get('alarm_type')
    start_str = request.args.get('start_time')
    end_str = request.args.get('end_time')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    query = AlarmLog.query

    if storage_id:
        query = query.filter_by(storage_id=storage_id)
    if is_handled is not None and is_handled != '':
        query = query.filter_by(is_handled=(is_handled in ('true', '1', 'True', True)))
    if alarm_type:
        query = query.filter(AlarmLog.alarm_type == alarm_type)
    if start_str:
        start_time = _parse_datetime_param(start_str, None)
        if start_time:
            query = query.filter(AlarmLog.triggered_at >= start_time)
    if end_str:
        end_time = _parse_datetime_param(end_str, None)
        if end_time:
            query = query.filter(AlarmLog.triggered_at < end_time)

    query = query.order_by(AlarmLog.triggered_at.desc())
    pagination = query.paginate(page=page, per_page=per_page)

    return jsonify({
        'total': pagination.total,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'pages': pagination.pages,
        'items': [a.to_dict() for a in pagination.items]
    })


@app.route('/export/alarms')
def export_alarms():
    storage_id = request.args.get('storage_id', type=int)
    is_handled = request.args.get('is_handled')
    alarm_type = request.args.get('alarm_type')
    start_str = request.args.get('start_time')
    end_str = request.args.get('end_time')

    query = AlarmLog.query
    if storage_id:
        query = query.filter_by(storage_id=storage_id)
    if is_handled is not None and is_handled != '':
        query = query.filter_by(is_handled=(is_handled in ('true', '1', 'True', True)))
    if alarm_type:
        query = query.filter(AlarmLog.alarm_type == alarm_type)
    if start_str:
        start_time = _parse_datetime_param(start_str, None)
        if start_time:
            query = query.filter(AlarmLog.triggered_at >= start_time)
    if end_str:
        end_time = _parse_datetime_param(end_str, None)
        if end_time:
            query = query.filter(AlarmLog.triggered_at < end_time)

    alarms = query.order_by(AlarmLog.triggered_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', '冷库名称', '报警类型', '报警内容', '当前值', '范围下限', '范围上限',
                     '触发时间', '是否已处理', '处理人', '处理时间', '处理备注'])

    for a in alarms:
        writer.writerow([
            a.id,
            a.storage.name if a.storage else '未知',
            '温度' if 'temp' in a.alarm_type else '湿度',
            a.message,
            a.current_value,
            a.threshold_min,
            a.threshold_max,
            a.triggered_at.strftime('%Y-%m-%d %H:%M:%S'),
            '是' if a.is_handled else '否',
            a.handled_by or '',
            a.handled_at.strftime('%Y-%m-%d %H:%M:%S') if a.handled_at else '',
            a.handled_note or ''
        ])

    output.seek(0)
    filename = f"alarm_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/api/manual/batch-validate', methods=['POST'])
def batch_validate():
    data = request.json or {}
    raw_text = data.get('raw_text', '')
    storage_id = data.get('storage_id')

    if not storage_id:
        return jsonify({'error': '请选择冷库'}), 400

    storage = ColdStorage.query.get(storage_id)
    if not storage:
        return jsonify({'error': '冷库不存在'}), 404

    lines = [line.strip() for line in raw_text.strip().split('\n') if line.strip()]
    valid_records = []
    invalid_rows = []
    alarm_preview = []

    for i, line in enumerate(lines, 1):
        parts = [p.strip() for p in line.replace('\t', ',').replace(';', ',').replace('|', ',').split(',')]
        parts = [p for p in parts if p]

        if len(parts) < 3:
            invalid_rows.append({
                'line_number': i,
                'original': line,
                'error': '字段不足，至少需要：时间,温度,湿度'
            })
            continue

        time_str = parts[0]
        temp_str = parts[1]
        hum_str = parts[2]

        record_time = None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M',
                    '%m-%d %H:%M', '%m/%d %H:%M'):
            try:
                record_time = datetime.strptime(time_str, fmt)
                if record_time.year == 1900:
                    record_time = record_time.replace(year=datetime.now().year)
                break
            except ValueError:
                continue

        if not record_time:
            invalid_rows.append({
                'line_number': i,
                'original': line,
                'error': f'时间格式无法识别："{time_str}"，支持 YYYY-MM-DD HH:MM:SS 等'
            })
            continue

        try:
            temperature = float(temp_str)
        except ValueError:
            invalid_rows.append({
                'line_number': i,
                'original': line,
                'error': f'温度无法解析为数字："{temp_str}"'
            })
            continue

        try:
            humidity = float(hum_str)
        except ValueError:
            invalid_rows.append({
                'line_number': i,
                'original': line,
                'error': f'湿度无法解析为数字："{hum_str}"'
            })
            continue

        alarms = []
        if temperature < storage.temp_min:
            alarms.append(f'温度过低({temperature}°C < {storage.temp_min}°C)')
        elif temperature > storage.temp_max:
            alarms.append(f'温度过高({temperature}°C > {storage.temp_max}°C)')

        if humidity < storage.humidity_min:
            alarms.append(f'湿度过低({humidity}% < {storage.humidity_min}%)')
        elif humidity > storage.humidity_max:
            alarms.append(f'湿度过高({humidity}% > {storage.humidity_max}%)')

        record = {
            'line_number': i,
            'recorded_at': record_time.strftime('%Y-%m-%d %H:%M:%S'),
            'temperature': temperature,
            'humidity': humidity,
            'has_alarm': len(alarms) > 0,
            'alarms': alarms
        }
        valid_records.append(record)

        if alarms:
            alarm_preview.append(record)

    return jsonify({
        'storage_name': storage.name,
        'total_lines': len(lines),
        'valid_count': len(valid_records),
        'invalid_count': len(invalid_rows),
        'alarm_count': len(alarm_preview),
        'valid_records': valid_records,
        'invalid_rows': invalid_rows,
        'alarm_preview': alarm_preview
    })


@app.route('/api/manual/batch-save', methods=['POST'])
def batch_save():
    data = request.json or {}
    storage_id = data.get('storage_id')
    records = data.get('records', [])
    note = data.get('note', '')

    if not storage_id:
        return jsonify({'error': '请选择冷库'}), 400

    storage = ColdStorage.query.get(storage_id)
    if not storage:
        return jsonify({'error': '冷库不存在'}), 404

    if not records:
        return jsonify({'error': '没有可导入的数据'}), 400

    batch = ImportBatch(
        storage_id=storage_id,
        batch_type='manual',
        imported_by=data.get('imported_by') or '手动导入',
        note=note
    )
    db.session.add(batch)
    db.session.flush()

    saved_count = 0
    created_alarms = []

    for r in records:
        try:
            record_time = datetime.strptime(r['recorded_at'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, KeyError):
            continue

        record = TemperatureRecord(
            storage_id=storage_id,
            batch_id=batch.id,
            temperature=float(r['temperature']),
            humidity=float(r['humidity']),
            is_manual=True,
            recorded_at=record_time
        )
        db.session.add(record)

        alarms = check_alarm(storage, float(r['temperature']), float(r['humidity']), record_time)
        for alarm in alarms:
            alarm.batch_id = batch.id
            db.session.add(alarm)
            db.session.flush()
            created_alarms.append(alarm.to_dict())
            send_simulated_email(alarm)

        saved_count += 1

    batch.record_count = saved_count
    batch.alarm_count = len(created_alarms)

    db.session.commit()

    return jsonify({
        'status': 'success',
        'saved_count': saved_count,
        'alarm_count': len(created_alarms),
        'alarms': created_alarms,
        'batch_id': batch.id
    })


@app.route('/api/active-alarms')
def active_alarms():
    alarms = AlarmLog.query.filter_by(is_handled=False).order_by(AlarmLog.triggered_at.desc()).all()
    return jsonify([a.to_dict() for a in alarms])


@app.route('/api/alarms/<int:alarm_id>')
def alarm_detail(alarm_id):
    alarm = AlarmLog.query.get_or_404(alarm_id)
    storage = ColdStorage.query.get(alarm.storage_id)

    before_records = TemperatureRecord.query.filter(
        TemperatureRecord.storage_id == alarm.storage_id,
        TemperatureRecord.recorded_at < alarm.triggered_at
    ).order_by(TemperatureRecord.recorded_at.desc()).limit(5).all()
    before_records = list(reversed(before_records))

    after_records = TemperatureRecord.query.filter(
        TemperatureRecord.storage_id == alarm.storage_id,
        TemperatureRecord.recorded_at >= alarm.triggered_at
    ).order_by(TemperatureRecord.recorded_at.asc()).limit(10).all()

    context_records = before_records + after_records
    context_data = [r.to_dict() for r in context_records]
    for r in context_data:
        r['is_alarm_point'] = False
        if alarm.alarm_type.startswith('temp') and r['id'] == after_records[0].id if after_records else False:
            pass

    related_alarms = AlarmLog.query.filter(
        AlarmLog.storage_id == alarm.storage_id,
        AlarmLog.triggered_at >= alarm.triggered_at - timedelta(hours=1),
        AlarmLog.triggered_at <= alarm.triggered_at + timedelta(hours=2)
    ).order_by(AlarmLog.triggered_at.asc()).all()

    result = alarm.to_dict()
    result['context_records'] = context_data
    result['context_alarms'] = [a.to_dict() for a in related_alarms]
    result['temp_range'] = {'min': storage.temp_min, 'max': storage.temp_max} if storage else None
    result['humidity_range'] = {'min': storage.humidity_min, 'max': storage.humidity_max} if storage else None

    return jsonify(result)


@app.route('/api/import-batches')
def api_import_batches():
    storage_id = request.args.get('storage_id', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = ImportBatch.query
    if storage_id:
        query = query.filter_by(storage_id=storage_id)
    query = query.order_by(ImportBatch.imported_at.desc())

    pagination = query.paginate(page=page, per_page=per_page)
    return jsonify({
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'items': [b.to_dict() for b in pagination.items]
    })


@app.route('/api/import-batches/<int:batch_id>/revoke', methods=['POST'])
def revoke_import_batch(batch_id):
    batch = ImportBatch.query.get_or_404(batch_id)

    if batch.is_revoked:
        return jsonify({'error': '该批次已撤回'}), 400

    try:
        AlarmLog.query.filter_by(batch_id=batch_id).delete()
        TemperatureRecord.query.filter_by(batch_id=batch_id).delete()

        batch.is_revoked = True
        batch.revoked_at = datetime.now()

        db.session.commit()

        return jsonify({
            'status': 'success',
            'message': f'批次撤回成功，删除了 {batch.record_count} 条记录和 {batch.alarm_count} 条报警'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'撤回失败：{str(e)}'}), 500


@app.route('/api/manual-records')
def api_manual_records():
    storage_id = request.args.get('storage_id', type=int)
    hours = request.args.get('hours', 168, type=int)
    limit = request.args.get('limit', 20, type=int)

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    query = TemperatureRecord.query.filter(
        TemperatureRecord.is_manual == True,
        TemperatureRecord.recorded_at >= start_time,
        TemperatureRecord.recorded_at <= end_time
    )
    if storage_id:
        query = query.filter_by(storage_id=storage_id)

    records = query.order_by(TemperatureRecord.recorded_at.desc()).limit(limit).all()

    result = []
    for r in records:
        item = r.to_dict()
        item['storage_name'] = r.storage.name if r.storage else '未知冷库'
        result.append(item)

    return jsonify(result)


@app.route('/api/storages')
def api_storages():
    storages = ColdStorage.query.all()
    return jsonify([s.to_dict() for s in storages])


def init_demo_data():
    storage_count = ColdStorage.query.count()
    record_count = TemperatureRecord.query.count()
    alarm_count = AlarmLog.query.count()

    if storage_count > 0 or record_count > 0 or alarm_count > 0:
        if storage_count > 0:
            print(f"数据持久化检查：检测到 {storage_count} 个冷库，跳过演示数据初始化")
        if record_count > 0:
            print(f"数据持久化检查：检测到 {record_count} 条温湿度记录，保留历史数据")
        if alarm_count > 0:
            print(f"数据持久化检查：检测到 {alarm_count} 条报警记录，保留历史数据")
        return

    demo_storages = [
        {'name': '肉类冷库1号', 'temp_min': -22, 'temp_max': -18, 'humidity_min': 85, 'humidity_max': 95},
        {'name': '海鲜冷库2号', 'temp_min': -25, 'temp_max': -20, 'humidity_min': 80, 'humidity_max': 90},
        {'name': '果蔬冷库3号', 'temp_min': 0, 'temp_max': 4, 'humidity_min': 90, 'humidity_max': 95}
    ]

    for data in demo_storages:
        storage = ColdStorage(**data)
        db.session.add(storage)
        db.session.flush()

        base_time = datetime.now() - timedelta(hours=24)
        for i in range(288):
            record_time = base_time + timedelta(minutes=i * 5)
            temperature, humidity = generate_sensor_data(storage)

            record = TemperatureRecord(
                storage_id=storage.id,
                temperature=temperature,
                humidity=humidity,
                is_manual=False,
                recorded_at=record_time
            )
            db.session.add(record)

            alarms = check_alarm(storage, temperature, humidity, record_time)
            for alarm in alarms:
                db.session.add(alarm)
                db.session.flush()
                send_simulated_email(alarm)

    db.session.commit()
    print("演示数据初始化完成！")


scheduler = BackgroundScheduler()


def start_scheduler():
    scheduler.add_job(auto_record_data, 'interval', minutes=5, id='auto_record')
    scheduler.start()
    print("定时任务已启动：每5分钟自动采集一次温湿度数据")


def migrate_db():
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)

    tables = inspector.get_table_names()

    if 'import_batch' not in tables:
        print("数据库迁移：创建 import_batch 表...")
        with db.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE import_batch (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    storage_id INTEGER NOT NULL,
                    batch_type VARCHAR(20) DEFAULT 'manual',
                    record_count INTEGER DEFAULT 0,
                    alarm_count INTEGER DEFAULT 0,
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    imported_by VARCHAR(100) DEFAULT '系统导入',
                    note VARCHAR(500),
                    is_revoked BOOLEAN DEFAULT 0,
                    revoked_at DATETIME,
                    FOREIGN KEY (storage_id) REFERENCES cold_storage (id)
                )
            """))
            conn.commit()
        print("数据库迁移：import_batch 表创建完成")

    if 'temperature_record' in tables:
        columns = [col['name'] for col in inspector.get_columns('temperature_record')]
        if 'batch_id' not in columns:
            print("数据库迁移：为 temperature_record 添加 batch_id 列...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE temperature_record ADD COLUMN batch_id INTEGER
                """))
                conn.commit()
            print("数据库迁移：temperature_record.batch_id 列添加完成")

    if 'alarm_log' in tables:
        columns = [col['name'] for col in inspector.get_columns('alarm_log')]
        if 'batch_id' not in columns:
            print("数据库迁移：为 alarm_log 添加 batch_id 列...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE alarm_log ADD COLUMN batch_id INTEGER
                """))
                conn.commit()
            print("数据库迁移：alarm_log.batch_id 列添加完成")


with app.app_context():
    db.create_all()
    migrate_db()
    init_demo_data()

if __name__ == '__main__':
    start_scheduler()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
else:
    start_scheduler()
