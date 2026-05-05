# dashboard.py — CINAX v2 Dashboard
# Lee los CSVs del volumen /data y los muestra en una página web
# Corre en el puerto 8080 (Railway lo detecta automáticamente)

import os
import io
import csv
from datetime import datetime
from flask import Flask, Response, send_file

app = Flask(__name__)

DATA_DIR       = "/data"
LOG_FILE       = f"{DATA_DIR}/cinax_paper.log"
SEÑALES_CSV    = f"{DATA_DIR}/cinax_señales.csv"
POSICIONES_CSV = f"{DATA_DIR}/cinax_posiciones.csv"

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def leer_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows   = list(reader)
    return rows, reader.fieldnames or []

def calcular_resumen(posiciones):
    cerradas = [p for p in posiciones if p.get("estado") == "CERRADA"]
    abiertas = [p for p in posiciones if p.get("estado") == "ABIERTA"]
    if not cerradas:
        return {"n": 0, "wr": 0, "pf": 0, "acum": 0, "abiertas": len(abiertas)}
    rets = [float(p["retorno"]) for p in cerradas]
    gana = [r for r in rets if r > 0]
    perd = [r for r in rets if r < 0]
    wr   = len(gana) / len(rets)
    pf   = sum(gana) / (abs(sum(perd)) + 1e-8)
    return {
        "n":        len(cerradas),
        "wr":       wr,
        "pf":       pf,
        "acum":     sum(rets),
        "abiertas": len(abiertas),
        "ret_med":  sum(rets) / len(rets),
    }

def leer_log_tail(n=60):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, encoding="utf-8") as f:
        lineas = f.readlines()
    return [l.rstrip() for l in lineas[-n:]]

def ultimo_update():
    if not os.path.exists(LOG_FILE):
        return "—"
    mtime = os.path.getmtime(LOG_FILE)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

# ══════════════════════════════════════════════════════════════
# HTML
# ══════════════════════════════════════════════════════════════

def html_page():
    posiciones, _ = leer_csv(POSICIONES_CSV)
    señales,    _ = leer_csv(SEÑALES_CSV)
    res           = calcular_resumen(posiciones)
    log_lines     = leer_log_tail(80)
    update        = ultimo_update()

    abiertas = [p for p in posiciones if p.get("estado") == "ABIERTA"]
    cerradas = [p for p in posiciones if p.get("estado") == "CERRADA"]
    cerradas.reverse()  # más recientes primero

    # ── KPIs ──────────────────────────────────────────────────
    wr_color  = "#00e5a0" if res["wr"] >= 0.6 else "#ff4d6d"
    pf_color  = "#00e5a0" if res["pf"] >= 1.5 else "#ff4d6d"
    ac_color  = "#00e5a0" if res.get("acum", 0) >= 0 else "#ff4d6d"

    kpi_html = f"""
    <div class="kpis">
      <div class="kpi">
        <span class="kpi-val" style="color:{wr_color}">{res['wr']:.1%}</span>
        <span class="kpi-lbl">Win Rate</span>
      </div>
      <div class="kpi">
        <span class="kpi-val" style="color:{pf_color}">{res['pf']:.2f}</span>
        <span class="kpi-lbl">Profit Factor</span>
      </div>
      <div class="kpi">
        <span class="kpi-val" style="color:{ac_color}">{res.get('acum',0)*100:+.1f}%</span>
        <span class="kpi-lbl">Retorno acum.</span>
      </div>
      <div class="kpi">
        <span class="kpi-val">{res['n']}</span>
        <span class="kpi-lbl">Trades cerrados</span>
      </div>
      <div class="kpi">
        <span class="kpi-val">{res['abiertas']}</span>
        <span class="kpi-lbl">Posiciones abiertas</span>
      </div>
      <div class="kpi">
        <span class="kpi-val">{len(señales)}</span>
        <span class="kpi-lbl">Señales evaluadas</span>
      </div>
    </div>
    """

    # ── Posiciones abiertas ───────────────────────────────────
    if abiertas:
        filas_ab = ""
        for p in abiertas:
            filas_ab += f"""<tr>
              <td>{p.get('entry_date','')}</td>
              <td>{p.get('dia_semana','')}</td>
              <td>{float(p.get('entry_price',0)):,.1f}</td>
              <td>{p.get('exit_date_esperado','')}</td>
              <td>{float(p.get('prob',0)):.4f}</td>
              <td><span class="badge open">ABIERTA</span></td>
            </tr>"""
        tabla_ab = f"""
        <h2>Posiciones Abiertas</h2>
        <table>
          <thead><tr>
            <th>Entrada</th><th>Día</th><th>Precio</th>
            <th>Exit esperado</th><th>Prob</th><th>Estado</th>
          </tr></thead>
          <tbody>{filas_ab}</tbody>
        </table>"""
    else:
        tabla_ab = "<h2>Posiciones Abiertas</h2><p class='empty'>Sin posiciones abiertas ahora mismo.</p>"

    # ── Historial de posiciones ───────────────────────────────
    filas_hist = ""
    for p in cerradas[:50]:
        ret    = float(p.get("retorno", 0))
        cls    = "win" if ret > 0 else "loss"
        emoji  = "✅" if ret > 0 else "❌"
        filas_hist += f"""<tr class="{cls}">
          <td>{p.get('entry_date','')}</td>
          <td>{p.get('dia_semana','')}</td>
          <td>{float(p.get('entry_price',0)):,.1f}</td>
          <td>{p.get('exit_date_esperado','')}</td>
          <td>{float(p.get('exit_price',0)):,.1f}</td>
          <td>{ret*100:+.2f}%</td>
          <td>{float(p.get('prob',0)):.4f}</td>
          <td>{emoji}</td>
        </tr>"""

    tabla_hist = f"""
    <h2>Historial de Trades</h2>
    <table>
      <thead><tr>
        <th>Entrada</th><th>Día</th><th>P. Entrada</th>
        <th>Exit esp.</th><th>P. Salida</th><th>Retorno</th>
        <th>Prob</th><th></th>
      </tr></thead>
      <tbody>{filas_hist if filas_hist else '<tr><td colspan="8" style="text-align:center;opacity:.4">Sin trades cerrados aún</td></tr>'}</tbody>
    </table>"""

    # ── Log ──────────────────────────────────────────────────
    log_html = "\n".join(
        f'<div class="log-line {"log-señal" if "★" in l else "log-err" if "✗" in l else "log-ok" if "✓" in l else ""}">{l}</div>'
        for l in reversed(log_lines)
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CINAX v2 — Dashboard</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;500;700&display=swap');

    :root {{
      --bg:      #0a0c0f;
      --surface: #111418;
      --border:  #1e2530;
      --text:    #c8d0dc;
      --muted:   #4a5568;
      --green:   #00e5a0;
      --red:     #ff4d6d;
      --gold:    #f5c842;
      --blue:    #4d9fff;
      --mono:    'IBM Plex Mono', monospace;
      --sans:    'IBM Plex Sans', sans-serif;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.6;
    }}

    /* Header */
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 32px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    .logo {{
      font-family: var(--mono);
      font-size: 18px;
      font-weight: 600;
      letter-spacing: 3px;
      color: var(--green);
    }}
    .logo span {{ color: var(--muted); font-weight: 400; }}
    .status-dot {{
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 8px var(--green);
      margin-right: 8px;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: .3; }}
    }}
    .update-time {{
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
    }}

    main {{ max-width: 1400px; margin: 0 auto; padding: 28px 32px; }}

    /* KPIs */
    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 1px;
      background: var(--border);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 32px;
    }}
    .kpi {{
      background: var(--surface);
      padding: 20px 24px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .kpi-val {{
      font-family: var(--mono);
      font-size: 26px;
      font-weight: 600;
      color: var(--text);
    }}
    .kpi-lbl {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--muted);
    }}

    /* Downloads */
    .downloads {{
      display: flex;
      gap: 10px;
      margin-bottom: 32px;
    }}
    .dl-btn {{
      font-family: var(--mono);
      font-size: 12px;
      padding: 8px 18px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--surface);
      color: var(--blue);
      text-decoration: none;
      transition: border-color .2s, background .2s;
    }}
    .dl-btn:hover {{
      border-color: var(--blue);
      background: #0d1a2e;
    }}

    /* Sections */
    h2 {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 2px;
      color: var(--muted);
      margin-bottom: 12px;
      margin-top: 32px;
    }}
    h2:first-child {{ margin-top: 0; }}

    /* Tables */
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: var(--mono);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    th {{
      text-align: left;
      padding: 8px 12px;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--muted);
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 9px 12px;
      border-bottom: 1px solid var(--border);
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr.win td {{ background: rgba(0,229,160,.03); }}
    tr.loss td {{ background: rgba(255,77,109,.03); }}
    tr:hover td {{ background: rgba(255,255,255,.03); }}

    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 3px;
      font-size: 10px;
      letter-spacing: 1px;
    }}
    .badge.open {{ background: rgba(77,159,255,.15); color: var(--blue); }}

    .empty {{
      color: var(--muted);
      font-size: 13px;
      padding: 12px 0;
    }}

    /* Log */
    .log-box {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      max-height: 360px;
      overflow-y: auto;
      font-family: var(--mono);
      font-size: 11px;
    }}
    .log-line {{ padding: 2px 0; color: var(--muted); white-space: pre-wrap; word-break: break-all; }}
    .log-señal {{ color: var(--gold); }}
    .log-ok {{ color: var(--green); }}
    .log-err {{ color: var(--red); }}

    /* refresh */
    .refresh-note {{
      font-size: 11px;
      color: var(--muted);
      text-align: right;
      margin-top: 24px;
      font-family: var(--mono);
    }}
  </style>
  <meta http-equiv="refresh" content="300">
</head>
<body>
  <header>
    <div class="logo">CINAX<span> v2</span></div>
    <div>
      <span class="status-dot"></span>
      <span class="update-line">Último update: <span class="update-time">{update}</span></span>
    </div>
  </header>

  <main>
    {kpi_html}

    <div class="downloads">
      <a class="dl-btn" href="/download/señales">⬇ señales.csv</a>
      <a class="dl-btn" href="/download/posiciones">⬇ posiciones.csv</a>
      <a class="dl-btn" href="/download/log">⬇ paper.log</a>
    </div>

    {tabla_ab}
    {tabla_hist}

    <h2>Log en vivo</h2>
    <div class="log-box">{log_html if log_html else '<div class="log-line">Sin logs aún.</div>'}</div>

    <p class="refresh-note">Auto-refresh cada 5 min</p>
  </main>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════
# RUTAS
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return Response(html_page(), mimetype="text/html")

@app.route("/download/señales")
def dl_señales():
    if not os.path.exists(SEÑALES_CSV):
        return "Archivo no disponible aún", 404
    return send_file(SEÑALES_CSV, as_attachment=True,
                     download_name="cinax_señales.csv", mimetype="text/csv")

@app.route("/download/posiciones")
def dl_posiciones():
    if not os.path.exists(POSICIONES_CSV):
        return "Archivo no disponible aún", 404
    return send_file(POSICIONES_CSV, as_attachment=True,
                     download_name="cinax_posiciones.csv", mimetype="text/csv")

@app.route("/download/log")
def dl_log():
    if not os.path.exists(LOG_FILE):
        return "Archivo no disponible aún", 404
    return send_file(LOG_FILE, as_attachment=True,
                     download_name="cinax_paper.log", mimetype="text/plain")

@app.route("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
