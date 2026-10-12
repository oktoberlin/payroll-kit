# Deploying PayrollKit on the shared VPS

Same pattern as the invoice, reply and handoff apps: a system user, a venv, an env file, one gunicorn
worker under systemd, an nginx site and a certbot certificate, with a database on the shared Postgres 17.
No Docker. Nothing here touches the other apps' services, databases or nginx sites.

Placeholders: domain `payroll.hookangles.com`, directory `/opt/payroll-hookangles`, port `8013`.
Check that the port is free first: `ss -ltn | grep 8013`.

## 1. DNS
Add an `A` record `payroll` -> the VPS IP. At Namecheap, `setHosts` replaces the whole zone: call
`getHosts` first and send back every existing record plus the new one.

## 2. User, code, venv
```bash
sudo useradd --system --create-home --home-dir /opt/payroll-hookangles --shell /usr/sbin/nologin payroll
sudo -u payroll git clone <repo-url> /opt/payroll-hookangles/backend
cd /opt/payroll-hookangles/backend
sudo -u payroll python3 -m venv .venv
sudo -u payroll .venv/bin/pip install -r requirements.txt
```
The repo root must be the Django project (where `manage.py` lives). If the repo has `backend/` inside it,
adjust `WorkingDirectory` and `ExecStart` in the unit.

## 3. Database (new `payroll` database on the existing Postgres)
```bash
sudo -u postgres psql -c "CREATE USER payroll WITH PASSWORD '<db-password>';"
sudo -u postgres psql -c "CREATE DATABASE payroll OWNER payroll;"
```

## 4. Environment file
```bash
sudo cp deploy/payroll.env.example /opt/payroll-hookangles/.env
sudo chown payroll:payroll /opt/payroll-hookangles/.env && sudo chmod 600 /opt/payroll-hookangles/.env
sudoedit /opt/payroll-hookangles/.env   # real secret key and DB password
```

## 5. Migrate, collect static, seed
```bash
cd /opt/payroll-hookangles/backend
sudo -u payroll bash -c 'set -a; . /opt/payroll-hookangles/.env; set +a
  .venv/bin/python manage.py migrate --noinput
  .venv/bin/python manage.py collectstatic --noinput
  .venv/bin/python manage.py seed_demo'
```

## 6. systemd
```bash
sudo cp deploy/payroll.service /etc/systemd/system/payroll.service
sudo systemctl daemon-reload && sudo systemctl enable --now payroll
curl -s http://127.0.0.1:8013/api/health/    # {"status": "ok"}
```
If you get `400 Bad Request`, the Host header isn't allowed: keep `localhost` in `DJANGO_ALLOWED_HOSTS`.

## 7. nginx and HTTPS
```bash
sudo cp deploy/nginx-payroll.conf /etc/nginx/sites-available/payroll.hookangles.com
sudo ln -s /etc/nginx/sites-available/payroll.hookangles.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d payroll.hookangles.com
```

## 8. Nightly demo reset
```bash
sudo cp deploy/payroll-demo-reset.cron /etc/cron.d/payroll-demo-reset
sudo chmod 644 /etc/cron.d/payroll-demo-reset
```
Check later with `journalctl -t payroll-demo-reset`. `seed_demo --reset` deletes and recreates the demo
company and the three demo users, so it must only ever read this app's `.env`.

## 9. Verify
- `https://payroll.hookangles.com/api/docs/` loads; log in as `demo_manager`, calculate the run.
- `systemctl status payroll invoices reply handoff` all active; the other sites still load.
- `free -m` and `systemctl status payroll` (Memory line) show the new footprint, expect about 100-150 MB.

## Updating
```bash
cd /opt/payroll-hookangles/backend && sudo -u payroll git pull
sudo -u payroll .venv/bin/pip install -r requirements.txt
sudo -u payroll bash -c 'set -a; . /opt/payroll-hookangles/.env; set +a
  .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart payroll
```
Restarting `payroll` does not affect the other services.
