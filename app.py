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


class TemperatureRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    storage_id = db.Column(db.Integer, db.ForeignKey('cold_storage.id'), nullable=False)
    temperature = db.Column(db.Float, nullable=False)
    humidity = db.Column(db.Float, nullable=False)
    is_manual = db.Column(db.Boolean, default=False)
    recorded_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'storage_id': self.storage_id,
            'temperature': self.temperature,
            'humidity': self.humidity,
            'is_manual': self.is_manual,
            'recorded_at': self.recorded_at.strftime('%Y-%m-%d %H:%M:%S')
        }


class AlarmLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    storage_id = db.Column(db.Integer, db.ForeignKey('cold_storage.id'), nullable=False)
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
    page = request.args.get('page', 1, type=int)
    per_page = 20
    alarms_query = AlarmLog.query.order_by(AlarmLog.triggered_at.desc())
    pagination = alarms_query.paginate(page=page, per_page=per_page)
    return render_template('alarms.html', pagination=pagination, alarms=pagination.items)


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

    export_start_date = start_time.strftime('%Y-%m-%d')
    export_end_date = (end_time - timedelta(seconds=1)).strftime('%Y-%m-%d')

    if not records:
        return jsonify({
            'storage_id': storage_id,
            'report_type': report_type,
            'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'export_start_date': export_start_date,
            'export_end_date': export_end_date,
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
        'export_start_date': export_start_date,
        'export_end_date': export_end_date,
        'summary': summary,
        'daily_data': daily_stats
    })


@app.route('/export/csv')
def export_csv():
    storage_id = request.args.get('storage_id', type=int)
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')

    start_time = datetime.strptime(start_str, '%Y-%m-%d') if start_str else datetime.now() - timedelta(days=7)
    end_time = datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=1) if end_str else datetime.now()

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


@app.route('/api/active-alarms')
def active_alarms():
    alarms = AlarmLog.query.filter_by(is_handled=False).order_by(AlarmLog.triggered_at.desc()).all()
    return jsonify([a.to_dict() for a in alarms])


@app.route('/api/alarms/<int:alarm_id>')
def alarm_detail(alarm_id):
    alarm = AlarmLog.query.get_or_404(alarm_id)
    return jsonify(alarm.to_dict())


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
    if ColdStorage.query.count() == 0:
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


with app.app_context():
    db.create_all()
    init_demo_data()

if __name__ == '__main__':
    start_scheduler()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
else:
    start_scheduler()
