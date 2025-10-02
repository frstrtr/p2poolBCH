# Установка p2pool на Ubuntu 24.04 (локально, PyPy/Python2)

Это краткое руководство по запуску p2pool на Ubuntu 24.04 с использованием PyPy2 и локальной сборки OpenSSL 1.1
(необходима для совместимости бинарных расширений cryptography/pyOpenSSL с PyPy2).

Краткие шаги

1. Запустите скрипт-установщик от root (sudo):

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh --user user0 \
  --rpc-host 192.168.86.200 --rpc-port 8332 --rpc-user p2poolrpcuser --rpc-pass <PASS> \
  --address <ВАШ_BCH_АДРЕС>
```

2. Скрипт установит необходимые пакеты сборки, распакует PyPy2 в домашний каталог, соберёт OpenSSL 1.1
и установит cryptography/pyOpenSSL под PyPy. Также будет создан wrapper и systemd unit для p2pool.

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

- Перед запуском сервиса убедитесь, что вы задали корректные RPC креды и явный адрес выплат (без префикса `bitcoincash:`)
  в `/etc/systemd/system/p2pool.service.d/override.conf` или в основном unit-файле.
- Скрипт настроен на локальную установку OpenSSL и PyPy в домашнюю директорию пользователя. Если вы изменяете пути,
  корректируйте переменные окружения и unit-файлы соответственно.

