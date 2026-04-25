# Установка p2pool на Ubuntu 24.04 (локально, PyPy/Python2)

Это краткое руководство по запуску p2pool на Ubuntu 24.04 с использованием PyPy2 и локальной сборки OpenSSL 1.1
(необходима для совместимости бинарных расширений cryptography/pyOpenSSL с PyPy2).

Краткие шаги

1. Запустите скрипт-установщик от root (sudo) — он запросит все настройки в интерактивном режиме:

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh
```

Либо передайте все значения аргументами (для автоматизированных установок):

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh \
  --user user0 --network bitcoincash \
  --rpc-host 192.168.86.200 --rpc-port 8332 \
  --rpc-user p2poolrpcuser --rpc-pass <PASS> \
  --address <ВАШ_BCH_АДРЕС> --yes
```

2. Скрипт запросит подтверждение, затем установит пакеты, распакует PyPy2, соберёт OpenSSL 1.1,
установит cryptography/pyOpenSSL под PyPy, создаст wrapper и systemd unit с реальными RPC-кредами и
адресом выплат, уже вписанными в `ExecStart` — редактировать unit вручную после установки не нужно.

Операционные дополнения, которые устанавливает скрипт

- chrony: устанавливается и включается сервис синхронизации времени.
- logrotate: файл `/etc/logrotate.d/p2pool` для ротации логов `/home/<user>/p2pool.out` и `/home/<user>/p2pool.log`.
- disk alert: `/usr/local/bin/p2pool-disk-alert.sh` и таймер systemd `p2pool-disk-alert.timer`, который исполняется
  ежечасно и при использовании диска `/` >= 90% пишет предупреждение в syslog.

Проверка после установки

```bash
# проверка состояния chrony
systemctl status chrony
chronyc tracking || chronyc sourcestats

# проверить конфигурацию logrotate для p2pool
logrotate -d /etc/logrotate.d/p2pool

# проверить таймер оповещения о дисковом пространстве
systemctl status p2pool-disk-alert.timer
journalctl -u p2pool-disk-alert.service -n 200 --no-pager

# статус и логи p2pool
systemctl show -p ExecStart p2pool.service
journalctl -u p2pool.service -n 200 --no-pager
```

Замечания

- RPC-креды и адрес выплат вписываются в `ExecStart` автоматически в процессе установки. Если при
  запуске скрипта пароль не был задан, добавьте drop-in override:
  `sudo systemctl edit p2pool.service` — и вставьте новую строку `ExecStart=` с нужным паролем.
- Скрипт настроен на локальную установку OpenSSL и PyPy в домашнюю директорию пользователя. Если
  вы изменяете пути, корректируйте переменные окружения и unit-файлы соответственно.

