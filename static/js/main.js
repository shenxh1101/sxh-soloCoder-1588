let lastAlarmCount = 0;

function checkActiveAlarms() {
    fetch('/api/active-alarms')
        .then(res => res.json())
        .then(alarms => {
            const alarmIndicator = document.getElementById('alarmIndicator');
            const alarmBanner = document.getElementById('alarmBanner');
            const alarmCount = document.getElementById('alarmCount');
            const alarmBannerText = document.getElementById('alarmBannerText');

            if (alarms.length > 0) {
                alarmIndicator.style.display = 'flex';
                alarmCount.textContent = alarms.length;

                if (alarms.length > lastAlarmCount) {
                    alarmBanner.style.display = 'block';
                    const latest = alarms[0];
                    alarmBannerText.textContent = `⚠️ ${latest.storage_name} - ${latest.message}`;
                    
                    showAlarmModal(alarms);
                    
                    if ('Notification' in window && Notification.permission === 'granted') {
                        new Notification('冷库温湿度报警', {
                            body: `${latest.storage_name} - ${latest.message}`,
                            icon: '❄️'
                        });
                    } else if ('Notification' in window && Notification.permission !== 'denied') {
                        Notification.requestPermission();
                    }
                }
            } else {
                alarmIndicator.style.display = 'none';
                alarmBanner.style.display = 'none';
            }

            lastAlarmCount = alarms.length;
        });
}

function showAlarmModal(alarms) {
    const modal = document.getElementById('alarmModal');
    const body = document.getElementById('alarmModalBody');

    let html = '<div class="alarm-items">';
    alarms.slice(0, 5).forEach(alarm => {
        const typeIcon = alarm.alarm_type.includes('temp') ? '🌡️' : '💧';
        const typeText = alarm.alarm_type.includes('temp') ? '温度' : '湿度';
        html += `
            <div class="alarm-modal-item">
                <div class="alarm-modal-header">
                    <span class="alarm-modal-type">${typeIcon} ${typeText}报警</span>
                    <span class="alarm-modal-time">${alarm.triggered_at}</span>
                </div>
                <div class="alarm-modal-storage">
                    <strong>${alarm.storage_name}</strong>
                </div>
                <div class="alarm-modal-message">
                    ${alarm.message}
                </div>
                <div class="alarm-modal-values">
                    <span>当前: <strong>${alarm.current_value}</strong></span>
                    <span>范围: ${alarm.threshold_min} ~ ${alarm.threshold_max}</span>
                </div>
                <button class="btn btn-small btn-primary mt-10" onclick="handleAlarmFromModal(${alarm.id})">处理此报警</button>
            </div>
        `;
    });
    if (alarms.length > 5) {
        html += `<p class="text-center text-muted mt-10">还有 ${alarms.length - 5} 条报警未显示</p>`;
    }
    html += '</div>';

    body.innerHTML = html;
    modal.style.display = 'flex';
}

function closeAlarmModal() {
    document.getElementById('alarmModal').style.display = 'none';
}

function handleAlarmFromModal(alarmId) {
    closeAlarmModal();
    openHandleAlarmModal(alarmId);
}

function openHandleAlarmModal(alarmId) {
    document.getElementById('handleAlarmId').value = alarmId;
    document.getElementById('handledBy').value = '';
    document.getElementById('handledNote').value = '';
    document.getElementById('handleAlarmModal').style.display = 'flex';
}

function closeHandleAlarmModal() {
    document.getElementById('handleAlarmModal').style.display = 'none';
}

function submitHandleAlarm() {
    const alarmId = document.getElementById('handleAlarmId').value;
    const handledBy = document.getElementById('handledBy').value.trim();
    const handledNote = document.getElementById('handledNote').value.trim();

    if (!handledBy) {
        alert('请输入处理人姓名');
        return;
    }

    fetch(`/alarms/${alarmId}/handle`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            handled_by: handledBy,
            handled_note: handledNote
        })
    }).then(res => res.json())
      .then(data => {
          if (data.status === 'success') {
              closeHandleAlarmModal();
              if (typeof showToast === 'function') {
                  showToast('报警处理成功！', 'success');
              } else {
                  alert('报警处理成功！');
              }
              setTimeout(() => location.reload(), 500);
          }
      });
}

document.addEventListener('DOMContentLoaded', function() {
    checkActiveAlarms();
    setInterval(checkActiveAlarms, 10000);

    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                this.style.display = 'none';
            }
        });
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal').forEach(modal => {
                modal.style.display = 'none';
            });
        }
    });
});

function formatDateTime(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function formatNumber(num, decimals = 1) {
    if (num === null || num === undefined) return '-';
    return parseFloat(num).toFixed(decimals);
}
