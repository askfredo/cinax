# CINAX v2 — Producción Diaria
# Guarda datos en /data (volumen Railway persistente)
# Envía resumen diario a Discord via webhook

import numpy as np
import pandas as pd
import yfinance as yf
import pickle
import os
import time
import warnings
import requests
from datetime import datetime
import pytz
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════

RUTA_PKL        = "modelo.pkl"
PCTIL           = 70
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

# ══════════════════════════════════════════════════════════════
# CONFIG FIJA
# ══════════════════════════════════════════════════════════════

ACTIVO       = "^GSPC"
WINDOW_PCT   = 252
MERCADO_TZ   = pytz.timezone("America/New_York")
CHECK_MINS   = 60
DIAS_ENTRADA = {0, 1, 2}
NOMBRES_DIA  = {0:"Lunes", 1:"Martes", 2:"Miércoles", 3:"Jueves", 4:"Viernes"}

MACRO_TICKERS = [
    "DX-Y.NYB","CL=F","HG=F","XLU","RSP","^VVIX","SMH","HYG",
    "GC=F","SPHB","SPLV","TLT","IEF","LQD",
    "^VIX","^VIX3M","XLK","XLF","XLI","XLP","XLV","^IRX","^TNX",
]

COLS_FIN = [
    "yield_3m_pct", "curve_2_10_pct", "curve_regime_pct",
    "rsi7_pct", "rsi14_pct", "rsi21_pct",
    "rsi14_slope_pct", "rsi_diverge_pct",
    "sma20_vs_200_pct", "dist_sma50_pct", "pos_52w",
    "vol_pct_10d", "vol_pct_20d", "vol_pct_30d", "vol_pct_60d", "vol_ratio_pct",
    "liq_shock_tlt_pct", "bond_eq_corr_pct",
    "mom_pct_5d", "mom_pct_10d", "mom_pct_20d", "mom_pct_60d",
]

DATA_DIR        = "/data"
LOG_FILE        = f"{DATA_DIR}/cinax_paper.log"
SEÑALES_CSV     = f"{DATA_DIR}/cinax_señales.csv"
POSICIONES_CSV  = f"{DATA_DIR}/cinax_posiciones.csv"
INTRA_CSV       = f"{DATA_DIR}/cinax_intra.csv"

os.makedirs(DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# LOG
# ══════════════════════════════════════════════════════════════

def log(msg, nivel="INFO"):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sym = {"INFO":"·","SEÑAL":"★","WARN":"!","ERR":"✗","OK":"✓"}.get(nivel, "·")
    txt = f"[{ts}] {sym} {msg}"
    print(txt, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(txt + "\n")

# ══════════════════════════════════════════════════════════════
# DISCORD
# ══════════════════════════════════════════════════════════════

def discord(mensaje):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": mensaje}, timeout=10)
    except Exception as e:
        log(f"Discord error: {e}", "WARN")

def discord_resumen_diario(fecha_barra, precio, prob, umbral, señal, cerradas_hoy=None):
    """Mensaje para días Lun/Mar/Mié con o sin señal."""
    hoy    = fecha_barra.strftime("%Y-%m-%d")
    dia    = NOMBRES_DIA.get(fecha_barra.weekday(), "")
    sp_fmt = f"{precio:,.1f}"

    if señal:
        viernes = next_friday(fecha_barra).strftime("%Y-%m-%d")
        header  = f"🟢 **CINAX — SEÑAL ACTIVA** | {hoy} ({dia})"
        detalle = (f"```\n"
                   f"S&P 500 Close : {sp_fmt}\n"
                   f"Probabilidad  : {prob:.4f}  (umbral {umbral:.4f})\n"
                   f"Entrada       : HOY al CLOSE\n"
                   f"Exit esperado : {viernes} (viernes al CLOSE)\n"
                   f"```")
    else:
        header  = f"⚪ **CINAX — Sin señal** | {hoy} ({dia})"
        detalle = (f"```\n"
                   f"S&P 500 Close : {sp_fmt}\n"
                   f"Probabilidad  : {prob:.4f}  (umbral {umbral:.4f})\n"
                   f"```")

    cierre_txt  = _bloque_cerradas(cerradas_hoy)
    resumen_txt = _bloque_acumulado()
    discord(f"{header}\n{detalle}{cierre_txt}{resumen_txt}")


def discord_seguimiento_posicion(fecha_barra, precio_actual, cerradas_hoy=None):
    """Mensaje diario de seguimiento cuando hay posiciones abiertas."""
    if not os.path.exists(POSICIONES_CSV):
        return

    df_pos   = pd.read_csv(POSICIONES_CSV)
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]

    cierre_txt = _bloque_cerradas(cerradas_hoy)

    if abiertas.empty and not cierre_txt:
        return  # nada que reportar

    hoy    = fecha_barra.strftime("%Y-%m-%d")
    dia    = NOMBRES_DIA.get(fecha_barra.weekday(), "")
    header = f"📊 **CINAX — Seguimiento** | {hoy} ({dia})"

    pos_txt = ""
    if not abiertas.empty:
        lineas = []
        for _, p in abiertas.iterrows():
            entry_price = float(p["entry_price"])
            ret_actual  = precio_actual / entry_price - 1
            exit_esp    = p["exit_date_esperado"]
            emoji = "🟢" if ret_actual >= 0 else "🔴"
            lineas.append(
                f"{emoji}  entry {p['entry_date']} @ {entry_price:,.1f}"
                f"  →  ahora {precio_actual:,.1f}"
                f"  ret {ret_actual*100:+.2f}%"
                f"  | exit {exit_esp}"
            )
        pos_txt = "\n**Posiciones abiertas:**\n```\n" + "\n".join(lineas) + "\n```"

    resumen_txt = _bloque_acumulado()
    discord(f"{header}{pos_txt}{cierre_txt}{resumen_txt}")


def _bloque_cerradas(cerradas_hoy):
    if cerradas_hoy is None or len(cerradas_hoy) == 0:
        return ""
    lineas = []
    for _, p in cerradas_hoy.iterrows():
        ret   = float(p["retorno"])
        emoji = "✅" if ret > 0 else "❌"
        lineas.append(f"{emoji}  entry {p['entry_date']}  →  exit HOY   ret {ret*100:+.2f}%")
    return "\n**Posiciones cerradas hoy:**\n```\n" + "\n".join(lineas) + "\n```"


def _bloque_acumulado():
    if not os.path.exists(POSICIONES_CSV):
        return ""
    df_all   = pd.read_csv(POSICIONES_CSV)
    cerradas = df_all[df_all["estado"] == "CERRADA"]
    abiertas = df_all[df_all["estado"] == "ABIERTA"]
    if len(cerradas) == 0:
        return ""
    rets = cerradas["retorno"].astype(float)
    wr   = (rets > 0).mean()
    pf   = rets[rets > 0].sum() / (abs(rets[rets < 0].sum()) + 1e-8)
    return (f"\n**Acumulado ({len(cerradas)} trades cerrados):**\n"
            f"```\n"
            f"Win Rate      : {wr:.1%}\n"
            f"Profit Factor : {pf:.2f}\n"
            f"Retorno acum  : {rets.sum()*100:+.1f}%\n"
            f"Abiertas ahora: {len(abiertas)}\n"
            f"```")

# ══════════════════════════════════════════════════════════════
# HORARIO
# ══════════════════════════════════════════════════════════════

def next_friday(date):
    days_ahead = 4 - date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return date + pd.Timedelta(days=days_ahead)

def mercado_cerrado_hoy():
    ahora = datetime.now(MERCADO_TZ)
    if ahora.weekday() >= 5:
        return False
    return ahora >= ahora.replace(hour=16, minute=5, second=0, microsecond=0)

def segundos_hasta_cierre():
    ahora = datetime.now(MERCADO_TZ)
    ci    = ahora.replace(hour=16, minute=5, second=0, microsecond=0)
    if ahora.weekday() < 5 and ahora < ci:
        return (ci - ahora).total_seconds()
    dias_extra = 1
    while True:
        prox = ahora + pd.Timedelta(days=dias_extra)
        if prox.weekday() < 5:
            return (prox.replace(hour=16, minute=5, second=0, microsecond=0) - ahora).total_seconds()
        dias_extra += 1

# ══════════════════════════════════════════════════════════════
# DATOS
# ══════════════════════════════════════════════════════════════

def descargar_datos():
    log("Descargando datos (desde 2000 para warmup)...")
    df_sp = yf.download(ACTIVO, start="2000-01-01", interval="1d",
                        auto_adjust=False, progress=False)
    if isinstance(df_sp.columns, pd.MultiIndex):
        df_sp.columns = df_sp.columns.droplevel(1)
    df_sp.columns = [c.lower() for c in df_sp.columns]

    df_macro = yf.download(MACRO_TICKERS, start="2000-01-01",
                           interval="1d", progress=False)
    if isinstance(df_macro.columns, pd.MultiIndex):
        df_macro = df_macro["Close"]
    df_macro = df_macro.rename(columns={
        "DX-Y.NYB":"dxy","CL=F":"oil","HG=F":"copper","XLU":"xlu",
        "RSP":"rsp","^VVIX":"vvix","SMH":"smh","HYG":"hyg",
        "GC=F":"gold","SPHB":"sphb","SPLV":"splv","TLT":"tlt",
        "IEF":"ief","LQD":"lqd","^VIX":"vix","^VIX3M":"vix3m",
        "XLK":"xlk","XLF":"xlf","XLI":"xli","XLP":"xlp","XLV":"xlv",
        "^IRX":"irx","^TNX":"tnx"
    })
    df_raw = df_sp.join(df_macro, how="left").ffill().fillna(0)
    df_raw.dropna(subset=["close","open","high","low"], inplace=True)
    log(f"✓ {len(df_raw)} barras cargadas")
    return df_raw

# ══════════════════════════════════════════════════════════════
# FEATURES  ← NO TOCAR
# ══════════════════════════════════════════════════════════════

def pctil_roll(series, w=WINDOW_PCT):
    return series.rolling(w, min_periods=60).rank(pct=True)

def build_features(d):
    d    = d.copy()
    ret1 = d["close"].pct_change(1)

    for n in [5, 10, 20, 60]:
        d[f"mom_pct_{n}d"] = pctil_roll(d["close"].pct_change(n))

    vols = {}
    for n in [10, 20, 30, 60]:
        v = ret1.rolling(n).std(); vols[n] = v
        d[f"vol_pct_{n}d"] = pctil_roll(v)
    d["vol_ratio_pct"] = pctil_roll(vols[10] / (vols[30] + 1e-8))

    rsis = {}
    for n in [7, 14, 21]:
        delta = d["close"].diff()
        gain  = delta.clip(lower=0).rolling(n).mean()
        loss  = (-delta.clip(upper=0)).rolling(n).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        d[f"rsi{n}"] = rsi; d[f"rsi{n}_pct"] = pctil_roll(rsi); rsis[n] = rsi
    d["rsi14_slope_pct"] = pctil_roll(rsis[14].diff(3))
    d["rsi_diverge_pct"] = pctil_roll(rsis[14].diff(3) - d["close"].pct_change(3) * 100)

    for n in [20, 30]:
        mid = d["close"].rolling(n).mean(); std = d["close"].rolling(n).std()
        d[f"bb{n}_pos_pct"]   = pctil_roll((d["close"] - (mid - 2*std)) / (4*std + 1e-8))
        d[f"bb{n}_width_pct"] = pctil_roll(4 * std / (mid + 1e-8))

    for n in [20, 50, 200]:
        d[f"sma{n}"] = d["close"].rolling(n).mean()

    sma20_vs_200 = d["sma20"] / d["sma200"] - 1
    dist_sma50   = (d["close"] - d["sma50"]) / d["sma50"]
    d["sma20_vs_200_pct"] = pctil_roll(sma20_vs_200)
    d["dist_sma50_pct"]   = pctil_roll(dist_sma50)
    d["pos_52w"]          = pctil_roll(d["close"])

    d["vix_pct"]        = pctil_roll(d["vix"])
    d["vix_zscore_pct"] = pctil_roll(
        (d["vix"] - d["vix"].rolling(60, min_periods=20).mean()) /
        (d["vix"].rolling(60, min_periods=20).std() + 1e-8))
    vts = d["vix"] / (d["vix3m"] + 1e-8)
    d["vix_ts_pct"]     = pctil_roll(vts)
    d["vix_spread_pct"] = pctil_roll(d["vix"] - d["vix3m"])

    d["dxy_shock_pct"]        = pctil_roll(d["dxy"].pct_change(3))
    d["oil_shock_pct"]        = pctil_roll(d["oil"].pct_change(5))
    d["credit_stress_pct"]    = pctil_roll(d["hyg"].pct_change(5))
    d["breadth_div_pct"]      = pctil_roll(d["close"].pct_change(5) - d["rsp"].pct_change(5))
    d["semi_lead_pct"]        = pctil_roll(d["smh"].pct_change(5) - d["close"].pct_change(5))
    d["macro_growth_pct"]     = pctil_roll((d["copper"] / d["gold"]).pct_change(5))
    d["risk_flow_pct"]        = pctil_roll((d["sphb"] / d["splv"]).pct_change(5))
    d["liq_shock_tlt_pct"]    = pctil_roll(d["tlt"].pct_change(3))
    d["bond_eq_corr_pct"]     = pctil_roll(ret1.rolling(10).corr(d["tlt"].pct_change(1)))
    d["credit_spread_ig_pct"] = pctil_roll(d["lqd"].pct_change(5) - d["tlt"].pct_change(5))
    d["yield_3m_pct"]         = pctil_roll(d["irx"] / 100)
    d["curve_2_10_pct"]       = pctil_roll(d["tnx"] / 100 - d["irx"] / 100)
    d["xlf_lead_pct"]         = pctil_roll(d["xlf"].pct_change(3) - d["close"].pct_change(3))
    d["tech_vs_staples_pct"]  = pctil_roll(d["xlk"].pct_change(5) - d["xlp"].pct_change(5))
    d["xlv_lead_pct"]         = pctil_roll(d["xlv"].pct_change(5) - d["close"].pct_change(5))

    ratio = d["sphb"] / (d["splv"] + 1e-8)
    d["h6_flow_pct"]      = pctil_roll(ratio.rolling(5).mean() / (ratio.rolling(20).mean() + 1e-8) - 1)
    d["vol_regime_pct"]   = pctil_roll(vols[30])
    d["trend_regime_pct"] = pctil_roll(sma20_vs_200)
    d["vix_regime_pct"]   = pctil_roll(d["vix"])
    d["curve_regime_pct"] = pctil_roll(d["tnx"] / 100 - d["irx"] / 100)

    d["rsi14_x_vol"]     = d["rsi14_pct"] * d["vol_regime_pct"]
    d["mom10_x_trend"]   = d["mom_pct_10d"] * d["trend_regime_pct"]
    d["vix_ts_x_vol"]    = d["vix_ts_pct"] * d["vol_regime_pct"]
    d["breadth_x_trend"] = d["breadth_div_pct"] * d["trend_regime_pct"]
    d["credit_x_vol"]    = d["credit_stress_pct"] * d["vol_regime_pct"]
    d["semi_x_trend"]    = d["semi_lead_pct"] * d["trend_regime_pct"]

    return d.dropna()

# ══════════════════════════════════════════════════════════════
# PREDICCIÓN  ← NO TOCAR
# ══════════════════════════════════════════════════════════════

def predecir(modelo, df_feat):
    fila   = df_feat.iloc[-1]
    fecha  = df_feat.index[-1]
    precio = float(fila["close"])
    X      = fila[COLS_FIN].fillna(0.5).values.reshape(1, -1)
    prob   = float(modelo.predict_proba(X)[:, 1][0])
    return prob, fecha, precio

# ══════════════════════════════════════════════════════════════
# REGISTROS
# ══════════════════════════════════════════════════════════════

def guardar_señal(fecha_barra, precio, prob, umbral, señal):
    nuevo = not os.path.exists(SEÑALES_CSV)
    with open(SEÑALES_CSV, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("timestamp,fecha_barra,dia_semana,precio,prob,umbral,señal\n")
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dia = fecha_barra.strftime("%A")
        f.write(f"{ts},{fecha_barra.date()},{dia},"
                f"{precio:.2f},{prob:.4f},{umbral:.4f},{int(señal)}\n")

def abrir_posicion(fecha_barra, precio_entrada, prob, umbral):
    exit_date = next_friday(fecha_barra)
    nuevo = not os.path.exists(POSICIONES_CSV)
    with open(POSICIONES_CSV, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("entry_date,dia_semana,entry_price,exit_date_esperado,"
                    "exit_price,retorno,prob,umbral,estado\n")
        dia = fecha_barra.strftime("%A")
        f.write(f"{fecha_barra.date()},{dia},{precio_entrada:.2f},"
                f"{exit_date.date()},,,{prob:.4f},{umbral:.4f},ABIERTA\n")
    log(f"Posición abierta | entry={fecha_barra.date()} | precio={precio_entrada:.2f} | "
        f"exit={exit_date.date()} (viernes) | prob={prob:.4f}", "SEÑAL")
    return exit_date

def registrar_barra_intra(df_raw, fecha_barra):
    """Guarda OHLC del día para posiciones abiertas."""
    if not os.path.exists(POSICIONES_CSV):
        return

    df_pos   = pd.read_csv(POSICIONES_CSV, parse_dates=["entry_date","exit_date_esperado"])
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if abiertas.empty:
        return

    hoy = fecha_barra.date()
    if hoy not in df_raw.index.date:
        return

    barra  = df_raw[df_raw.index.date == hoy].iloc[-1]
    open_  = float(barra["open"])
    high_  = float(barra["high"])
    low_   = float(barra["low"])
    close_ = float(barra["close"])

    if os.path.exists(INTRA_CSV):
        df_intra = pd.read_csv(INTRA_CSV)
    else:
        df_intra = pd.DataFrame(columns=["entry_date","fecha_barra","dia_semana",
                                          "open","high","low","close",
                                          "ret_vs_entry","ret_diario"])

    nuevas = []
    for _, pos in abiertas.iterrows():
        entry_date = pos["entry_date"].date()
        exit_date  = pos["exit_date_esperado"].date()

        if hoy <= entry_date or hoy > exit_date:
            continue

        ya_existe = (
            (df_intra["entry_date"].astype(str) == str(entry_date)) &
            (df_intra["fecha_barra"].astype(str) == str(hoy))
        ).any() if len(df_intra) > 0 else False

        if ya_existe:
            continue

        entry_price  = float(pos["entry_price"])
        ret_vs_entry = close_ / entry_price - 1
        dia_str      = fecha_barra.strftime("%A")

        nuevas.append({
            "entry_date":   str(entry_date),
            "fecha_barra":  str(hoy),
            "dia_semana":   dia_str,
            "open":         round(open_, 2),
            "high":         round(high_, 2),
            "low":          round(low_, 2),
            "close":        round(close_, 2),
            "ret_vs_entry": round(ret_vs_entry, 6),
            "ret_diario":   round(close_ / open_ - 1, 6),
        })
        log(f"Intra guardada | pos={entry_date} | barra={hoy} | "
            f"O={open_:.1f} H={high_:.1f} L={low_:.1f} C={close_:.1f} | "
            f"ret_entry={ret_vs_entry:+.2%}")

    if nuevas:
        df_new   = pd.DataFrame(nuevas)
        df_intra = pd.concat([df_intra, df_new], ignore_index=True)
        df_intra.to_csv(INTRA_CSV, index=False)

def cerrar_posiciones_vencidas(df_feat, df_raw):
    if not os.path.exists(POSICIONES_CSV):
        return pd.DataFrame()
    df_pos   = pd.read_csv(POSICIONES_CSV, parse_dates=["entry_date","exit_date_esperado"])
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if abiertas.empty:
        return pd.DataFrame()

    hoy          = df_feat.index[-1].date()
    cerradas_hoy = []

    for idx_pos, pos in abiertas.iterrows():
        exit_esp = pos["exit_date_esperado"].date()
        if hoy >= exit_esp:
            fechas_post = df_feat.index.date[df_feat.index.date >= exit_esp]
            if len(fechas_post) == 0:
                continue
            fecha_real     = fechas_post[0]
            precio_cierre  = float(df_feat[df_feat.index.date == fecha_real]["close"].iloc[-1])
            precio_entrada = float(pos["entry_price"])
            retorno        = precio_cierre / precio_entrada - 1

            df_pos.at[idx_pos, "exit_price"] = round(precio_cierre, 2)
            df_pos.at[idx_pos, "retorno"]    = round(retorno, 6)
            df_pos.at[idx_pos, "estado"]     = "CERRADA"
            cerradas_hoy.append(df_pos.loc[idx_pos].to_dict())

            log(f"Posición cerrada | entry={pos['entry_date'].date()} "
                f"→ exit={fecha_real} | retorno={retorno:+.2%} | "
                f"{'GANADORA ✓' if retorno > 0 else 'PERDEDORA ✗'}", "OK")

    df_pos.to_csv(POSICIONES_CSV, index=False)
    return pd.DataFrame(cerradas_hoy) if cerradas_hoy else pd.DataFrame()

def resumen_log():
    if not os.path.exists(POSICIONES_CSV):
        return
    df_pos   = pd.read_csv(POSICIONES_CSV)
    cerradas = df_pos[df_pos["estado"] == "CERRADA"]
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if cerradas.empty:
        log(f"Sin posiciones cerradas aún | Abiertas: {len(abiertas)}")
        return
    rets = cerradas["retorno"].astype(float)
    wr   = (rets > 0).mean()
    pf   = rets[rets > 0].sum() / (abs(rets[rets < 0].sum()) + 1e-8)
    log(f"RESUMEN | Cerradas: {len(cerradas)} | Abiertas: {len(abiertas)} | "
        f"WR: {wr:.1%} | PF: {pf:.2f} | Acum: {rets.sum()*100:+.1f}%")

# ══════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════

def main():
    log("=" * 60)
    log("CINAX v2 — Producción Diaria")
    log(f"Config: percentil={PCTIL} | entrada=Lun/Mar/Mié | cierre=viernes al CLOSE")
    log(f"Datos en: {DATA_DIR}")
    log(f"Discord: {'configurado ✓' if DISCORD_WEBHOOK else 'NO configurado'}")
    log("=" * 60)

    if not os.path.exists(RUTA_PKL):
        log(f"ERROR: No se encontró el .pkl en: {RUTA_PKL}", "ERR")
        return

    with open(RUTA_PKL, "rb") as f:
        modelo = pickle.load(f)
    log(f"Modelo cargado: {RUTA_PKL}", "OK")

    ultima_fecha_evaluada = None

    while True:
        try:
            ahora_et   = datetime.now(MERCADO_TZ)
            dia_semana = ahora_et.weekday()

            if dia_semana >= 5:
                secs = segundos_hasta_cierre()
                log(f"Fin de semana. Próxima evaluación en {secs/3600:.1f}h", "WARN")
                time.sleep(secs + 60)
                continue

            if not mercado_cerrado_hoy():
                secs = segundos_hasta_cierre()
                log(f"Esperando cierre en {secs/60:.0f} min...", "WARN")
                time.sleep(min(secs, CHECK_MINS * 60))
                continue

            log("Descargando datos y calculando features...")
            df_raw  = descargar_datos()
            df_feat = build_features(df_raw)

            if df_feat.empty:
                log("DataFrame vacío — reintentando en 5 min.", "WARN")
                time.sleep(300)
                continue

            prob, fecha_barra, precio = predecir(modelo, df_feat)
            X_hist  = df_feat[COLS_FIN].fillna(0.5).values
            probs_h = modelo.predict_proba(X_hist)[:, 1]
            umbral  = float(np.percentile(probs_h, PCTIL))

            # ── Registrar OHLC intra para posiciones abiertas (todos los días) ──
            registrar_barra_intra(df_raw, fecha_barra)

            if fecha_barra == ultima_fecha_evaluada:
                secs = segundos_hasta_cierre()
                log(f"Barra {fecha_barra.date()} ya evaluada. Próxima en {secs/3600:.1f}h")
                time.sleep(CHECK_MINS * 60)
                continue

            cerradas_hoy = cerrar_posiciones_vencidas(df_feat, df_raw)

            # ── Lun / Mar / Mié: evaluar señal ──────────────────────────────
            if dia_semana in DIAS_ENTRADA:
                señal = prob >= umbral
                info  = (f"{fecha_barra.date()} ({fecha_barra.strftime('%A')}) | "
                         f"Close: {precio:.1f} | Prob: {prob:.4f} | Umbral: {umbral:.4f}")

                if señal:
                    log(f"★ SEÑAL LARGA ★ — {info}", "SEÑAL")
                    log(f"  → Exit: {next_friday(fecha_barra).date()} (viernes al CLOSE)", "SEÑAL")
                    abrir_posicion(fecha_barra, precio, prob, umbral)
                else:
                    log(f"Sin señal — {info}")

                guardar_señal(fecha_barra, precio, prob, umbral, señal)
                resumen_log()
                discord_resumen_diario(
                    fecha_barra, precio, prob, umbral, señal,
                    cerradas_hoy if len(cerradas_hoy) > 0 else None
                )

            # ── Jueves: seguimiento de posición abierta ──────────────────────
            elif dia_semana == 3:
                resumen_log()
                discord_seguimiento_posicion(
                    fecha_barra, precio,
                    cerradas_hoy if len(cerradas_hoy) > 0 else None
                )

            # ── Viernes: cerrar vencidas + seguimiento ────────────────────────
            elif dia_semana == 4:
                resumen_log()
                if len(cerradas_hoy) > 0:
                    discord_seguimiento_posicion(
                        fecha_barra, precio,
                        cerradas_hoy
                    )

            ultima_fecha_evaluada = fecha_barra
            secs = segundos_hasta_cierre()
            log(f"Próxima evaluación en {secs/3600:.1f}h")
            time.sleep(secs + 120)

        except KeyboardInterrupt:
            log("Detenido por usuario.", "WARN")
            resumen_log()
            break
        except Exception as e:
            import traceback
            log(f"Error: {e}", "ERR")
            log(traceback.format_exc(), "ERR")
            time.sleep(60)


if __name__ == "__main__":
    main()
