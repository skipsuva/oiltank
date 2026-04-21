# Next Steps

## Done

- [x] Calibrate the detector (`calibration.json` tuned, detection confirmed working)
- [x] Implement `notify.py` — ntfy.sh push alerts on low level or detection failure
- [x] Web dashboard (`web.py`) — Chart.js graph of readings over time, served on port 8080
- [x] Systemd service — `oiltank-web` keeps the dashboard running across reboots
- [x] Cron job — pipeline runs at 8am and 8pm daily, output logged to `logs/cron.log`

---

## Remaining

### 1. Tailscale (remote access)

Install Tailscale on the Pi so the dashboard is reachable from anywhere:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the auth link, then install the Tailscale app on your phone. The dashboard will be available at `http://<pi-tailscale-ip>:8080`.

---

### 2. Configure ntfy alerts

Edit `config.json` to set your ntfy topic and alert threshold:

```json
{
  "ntfy_topic": "https://ntfy.sh/your-secret-topic",
  "low_threshold": 0.25
}
```

- Subscribe to the same topic in the ntfy app on your phone
- `low_threshold` is a 0.0–1.0 fraction — `0.25` means alert when tank is at or below 1/4

---

### 3. Optional improvements

- **Alert deduplication** — avoid repeat low-level alerts across consecutive readings when the tank is already known to be low (e.g. track last-alerted level in a state file)
- **Annotated image in dashboard** — show the latest `*_annotated.jpg` alongside the chart
- **Reading history limit** — cap the chart to the last N days rather than all time
