System-level setup still required on the VPS because this session cannot run sudo.

Prerequisites:
- Point `sms-clips.samuel.ovh` DNS to `137.74.12.119`
- Stop any existing service currently binding `443` if it should not remain public

Install and enable the app:

```bash
sudo cp /home/bot/ia-kissing-pipeline/deploy/systemd/ia-kissing-web.service /etc/systemd/system/ia-kissing-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now ia-kissing-web.service
```

Install nginx:

```bash
sudo apt-get update
sudo apt-get install -y nginx
```

Install the site config:

```bash
sudo cp /home/bot/ia-kissing-pipeline/deploy/nginx/sms-clips.samuel.ovh.conf /etc/nginx/sites-available/sms-clips.samuel.ovh.conf
sudo ln -sf /etc/nginx/sites-available/sms-clips.samuel.ovh.conf /etc/nginx/sites-enabled/sms-clips.samuel.ovh.conf
sudo nginx -t
sudo systemctl reload nginx
```

Optional HTTPS with Let's Encrypt after DNS resolves:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d sms-clips.samuel.ovh
```

Verification:

```bash
curl -I http://sms-clips.samuel.ovh
curl -I http://127.0.0.1:8000/films
systemctl status ia-kissing-web.service
systemctl status nginx
```
