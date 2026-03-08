"""
╔══════════════════════════════════════════════════════════════╗
║           MENTOR INVERSIONES — Monitor de Alertas           ║
║         Análisis automático + Notificaciones Email          ║
╚══════════════════════════════════════════════════════════════╝

INSTRUCCIONES RÁPIDAS:
1. Instala dependencias:  pip install yfinance pandas numpy
2. Configura tu email en la sección CONFIGURACIÓN (líneas 40-55)
3. Añade tus empresas en LISTA DE EMPRESAS (línea 60)
4. Ejecuta:  python mentor_alertas.py
"""

import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import json
import os
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — EDITA ESTOS DATOS
# ══════════════════════════════════════════════════════════════

EMAIL_ORIGEN  = "tu_email@gmail.com"       # Tu Gmail desde el que se envía
EMAIL_DESTINO = "tu_email@gmail.com"       # Dónde quieres recibir los avisos
EMAIL_PASSWORD = "xxxx xxxx xxxx xxxx"     # Contraseña de aplicación Gmail (ver guía abajo)

# ¿Cada cuántos minutos revisa el mercado?
INTERVALO_MINUTOS = 60  # Recomendado: 60 (cada hora en días de mercado)

# ══════════════════════════════════════════════════════════════
# LISTA DE EMPRESAS — Añade o quita las que quieras
# Formato: ("TICKER_YAHOO", "Nombre que quieres ver en el email")
# ══════════════════════════════════════════════════════════════

EMPRESAS = [
    ("ORCL",  "Oracle"),
    ("AAPL",  "Apple"),
    ("MSFT",  "Microsoft"),
    ("GOOGL", "Google"),
    ("AMZN",  "Amazon"),
    ("NVDA",  "Nvidia"),
    # Españolas (añade ".MC" para bolsa de Madrid):
    # ("SAN.MC",  "Santander"),
    # ("ITX.MC",  "Inditex"),
    # ("IBE.MC",  "Iberdrola"),
]

# ══════════════════════════════════════════════════════════════
# MOTOR DE ANÁLISIS
# ══════════════════════════════════════════════════════════════

def calcular_ema(serie, periodo):
    return serie.ewm(span=periodo, adjust=False).mean()

def calcular_rsi(serie, periodo=14):
    delta = serie.diff()
    ganancia = delta.clip(lower=0).rolling(periodo).mean()
    perdida  = (-delta.clip(upper=0)).rolling(periodo).mean()
    rs = ganancia / perdida.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calcular_atr(df, periodo=14):
    high, low, close_prev = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()

def analizar_empresa(ticker, nombre):
    try:
        datos = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
        if datos.empty or len(datos) < 50:
            return None

        # Aplanar columnas si son MultiIndex
        if isinstance(datos.columns, pd.MultiIndex):
            datos.columns = datos.columns.get_level_values(0)

        close  = datos["Close"]
        volume = datos["Volume"]

        ema200   = calcular_ema(close, 200)
        ema50    = calcular_ema(close, 50)
        rsi      = calcular_rsi(close, 14)
        atr      = calcular_atr(datos, 14)
        vol_sma  = volume.rolling(20).mean()

        precio_actual = round(float(close.iloc[-1]), 2)
        ema200_actual = round(float(ema200.iloc[-1]), 2)
        ema50_actual  = round(float(ema50.iloc[-1]),  2)
        rsi_actual    = round(float(rsi.iloc[-1]),    1)
        atr_actual    = float(atr.iloc[-1])
        vol_actual    = float(volume.iloc[-1])
        vol_media     = float(vol_sma.iloc[-1])

        # Tendencia
        alcista        = precio_actual > ema200_actual
        tendencia_txt  = "ALCISTA ▲" if alcista else "BAJISTA ▼"

        # Volumen fuerte
        volumen_fuerte = vol_actual > vol_media * 1.3

        # RSI cruce (comparando últimas 2 velas)
        rsi_ayer    = float(rsi.iloc[-2])
        rsi_cruce_al = rsi_ayer < 40 and rsi_actual >= 40
        rsi_cruce_ba = rsi_ayer > 60 and rsi_actual <= 60

        # Vela confirmación
        open_hoy  = float(datos["Open"].iloc[-1])
        close_hoy = precio_actual
        vela_al   = close_hoy > open_hoy and (close_hoy - open_hoy) > atr_actual * 0.5
        vela_ba   = close_hoy < open_hoy and (open_hoy - close_hoy) > atr_actual * 0.5

        # Señales
        entra = alcista and rsi_cruce_al and volumen_fuerte and vela_al
        sal   = not alcista and rsi_cruce_ba and volumen_fuerte and vela_ba

        # Niveles SL / TP
        sl = round(precio_actual - atr_actual * 1.5, 2)
        tp = round(precio_actual + atr_actual * 3.0, 2)

        # Nivel de espera (próximo soporte/resistencia relevante)
        if alcista:
            precio_espera = round(max(ema50_actual, precio_actual * 0.97), 2)
            accion_espera = f"si retrocede a ${precio_espera} y rebota, considera entrar"
        else:
            precio_espera = round(ema200_actual, 2)
            accion_espera = f"espera a que supere ${precio_espera} (EMA200)"

        return {
            "ticker":         ticker,
            "nombre":         nombre,
            "precio":         precio_actual,
            "ema200":         ema200_actual,
            "ema50":          ema50_actual,
            "rsi":            rsi_actual,
            "tendencia":      tendencia_txt,
            "alcista":        alcista,
            "volumen_fuerte": volumen_fuerte,
            "entra":          entra,
            "sal":            sal,
            "sl":             sl,
            "tp":             tp,
            "precio_espera":  precio_espera,
            "accion_espera":  accion_espera,
        }

    except Exception as e:
        print(f"  ⚠️  Error analizando {ticker}: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# GENERADOR DE EMAIL
# ══════════════════════════════════════════════════════════════

def generar_email(resultados, solo_señales=False):
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")

    señales  = [r for r in resultados if r["entra"] or r["sal"]]
    normales = [r for r in resultados if not r["entra"] and not r["sal"]]

    html = f"""
    <html><body style="font-family: Arial, sans-serif; background:#f5f5f5; padding:20px;">
    <div style="max-width:600px; margin:auto;">

      <div style="background:#1a1a2e; color:white; padding:20px; border-radius:10px 10px 0 0; text-align:center;">
        <h2 style="margin:0;">📊 MENTOR INVERSIONES</h2>
        <p style="margin:5px 0; color:#aaa; font-size:13px;">Análisis automático · {ahora}</p>
      </div>
    """

    # — SEÑALES ACTIVAS —
    if señales:
        html += """
      <div style="background:#fff; padding:20px; border-left:4px solid #e74c3c;">
        <h3 style="margin:0 0 15px; color:#e74c3c;">🚨 SEÑALES ACTIVAS</h3>
        """
        for r in señales:
            if r["entra"]:
                color_bg, color_borde, icono, accion = "#e8f5e9", "#27ae60", "🟢", "ENTRA"
                detalle = f"<b>Stop Loss:</b> ${r['sl']} &nbsp;|&nbsp; <b>Take Profit:</b> ${r['tp']}"
            else:
                color_bg, color_borde, icono, accion = "#fdecea", "#e74c3c", "🔴", "SAL"
                detalle = f"<b>Stop Loss:</b> ${r['sl']} &nbsp;|&nbsp; <b>Take Profit:</b> ${r['tp']}"

            html += f"""
        <div style="background:{color_bg}; border-left:4px solid {color_borde}; padding:12px; margin:10px 0; border-radius:4px;">
          <b style="font-size:16px;">{icono} {r['nombre']} ({r['ticker']})</b><br>
          <span style="font-size:22px; font-weight:bold; color:{color_borde};">{accion}</span>
          &nbsp;&nbsp;→&nbsp; Precio actual: <b>${r['precio']}</b><br>
          <span style="font-size:13px; color:#555;">{detalle}</span>
        </div>
            """
        html += "</div>"
    else:
        html += """
      <div style="background:#fff3cd; padding:15px; border-left:4px solid #f39c12;">
        <b>⏳ Sin señales activas hoy</b> — El mercado no presenta condiciones claras de entrada o salida.
      </div>
        """

    # — RESUMEN DE TODAS LAS EMPRESAS —
    html += """
      <div style="background:#fff; padding:20px; margin-top:2px;">
        <h3 style="margin:0 0 15px; color:#333;">📋 Estado de tu cartera</h3>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
          <tr style="background:#f0f0f0;">
            <th style="padding:8px; text-align:left;">Empresa</th>
            <th style="padding:8px; text-align:right;">Precio</th>
            <th style="padding:8px; text-align:center;">Tendencia</th>
            <th style="padding:8px; text-align:right;">RSI</th>
            <th style="padding:8px; text-align:left;">Qué hacer</th>
          </tr>
    """

    for i, r in enumerate(resultados):
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        color_tend = "#27ae60" if r["alcista"] else "#e74c3c"

        if r["entra"]:
            accion_txt = f"✅ ENTRA → SL ${r['sl']} / TP ${r['tp']}"
            color_acc  = "#27ae60"
        elif r["sal"]:
            accion_txt = f"❌ SAL → SL ${r['sl']} / TP ${r['tp']}"
            color_acc  = "#e74c3c"
        else:
            accion_txt = f"⏳ ESPERA — {r['accion_espera']}"
            color_acc  = "#888"

        html += f"""
          <tr style="background:{bg};">
            <td style="padding:8px; font-weight:bold;">{r['nombre']}<br><span style="color:#aaa; font-size:11px;">{r['ticker']}</span></td>
            <td style="padding:8px; text-align:right; font-weight:bold;">${r['precio']}</td>
            <td style="padding:8px; text-align:center; color:{color_tend}; font-size:12px;">{r['tendencia']}</td>
            <td style="padding:8px; text-align:right;">{r['rsi']}</td>
            <td style="padding:8px; color:{color_acc}; font-size:12px;">{accion_txt}</td>
          </tr>
        """

    html += """
        </table>
      </div>

      <div style="background:#1a1a2e; color:#888; padding:12px; border-radius:0 0 10px 10px; font-size:11px; text-align:center;">
        ⚠️ Esto es orientativo, no asesoramiento financiero. Usa siempre tu propio criterio.
      </div>

    </div></body></html>
    """

    hay_señales = len(señales) > 0
    asunto = f"🚨 MENTOR: {len(señales)} señal(es) activa(s) — {ahora}" if hay_señales else f"📊 MENTOR: Resumen diario — {ahora}"
    return asunto, html


# ══════════════════════════════════════════════════════════════
# ENVÍO DE EMAIL
# ══════════════════════════════════════════════════════════════

def enviar_email(asunto, html):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = EMAIL_ORIGEN
        msg["To"]      = EMAIL_DESTINO
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ORIGEN, EMAIL_DESTINO, msg.as_string())

        print(f"  ✅ Email enviado: {asunto}")
        return True
    except Exception as e:
        print(f"  ❌ Error enviando email: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# CONTROL DE SEÑALES (evita spam — solo avisa 1 vez por señal)
# ══════════════════════════════════════════════════════════════

ARCHIVO_ESTADO = "mentor_estado.json"

def cargar_estado():
    if os.path.exists(ARCHIVO_ESTADO):
        with open(ARCHIVO_ESTADO, "r") as f:
            return json.load(f)
    return {}

def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w") as f:
        json.dump(estado, f)

def es_señal_nueva(ticker, tipo_señal, estado):
    clave = f"{ticker}_{tipo_señal}"
    hoy   = datetime.now().strftime("%Y-%m-%d")
    return estado.get(clave) != hoy

def marcar_señal_enviada(ticker, tipo_señal, estado):
    estado[f"{ticker}_{tipo_señal}"] = datetime.now().strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════════════════════════════

def ejecutar_ciclo():
    print(f"\n{'═'*55}")
    print(f"  🔍 Analizando mercado — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'═'*55}")

    estado     = cargar_estado()
    resultados = []
    señales_nuevas = []

    for ticker, nombre in EMPRESAS:
        print(f"  → {nombre} ({ticker})...")
        resultado = analizar_empresa(ticker, nombre)
        if resultado:
            resultados.append(resultado)

            # Detectar si es señal nueva (no enviada hoy)
            if resultado["entra"] and es_señal_nueva(ticker, "entra", estado):
                señales_nuevas.append(resultado)
                marcar_señal_enviada(ticker, "entra", estado)
                print(f"     🟢 SEÑAL ENTRA detectada!")
            elif resultado["sal"] and es_señal_nueva(ticker, "sal", estado):
                señales_nuevas.append(resultado)
                marcar_señal_enviada(ticker, "sal", estado)
                print(f"     🔴 SEÑAL SAL detectada!")
            else:
                accion = "ESPERA"
                print(f"     ⏳ {accion} — RSI: {resultado['rsi']} | {resultado['tendencia']}")

    guardar_estado(estado)

    # Enviar email si hay señales nuevas O una vez al día como resumen
    ultima_revision = estado.get("ultimo_resumen_diario")
    hoy = datetime.now().strftime("%Y-%m-%d")

    if señales_nuevas:
        print(f"\n  📧 Enviando alerta de {len(señales_nuevas)} señal(es)...")
        asunto, html = generar_email(resultados)
        enviar_email(asunto, html)
    elif ultima_revision != hoy and datetime.now().hour >= 18:
        # Resumen diario al final del día (después de las 18h)
        print(f"\n  📧 Enviando resumen diario...")
        asunto, html = generar_email(resultados)
        enviar_email(asunto, html)
        estado["ultimo_resumen_diario"] = hoy
        guardar_estado(estado)
    else:
        print(f"\n  ✅ Sin señales nuevas. Próxima revisión en {INTERVALO_MINUTOS} min.")


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║         MENTOR INVERSIONES — Monitor Activo             ║
║   Revisando cada {intervalo} minutos · Ctrl+C para parar   ║
╚══════════════════════════════════════════════════════════╝
    """.format(intervalo=INTERVALO_MINUTOS))

    while True:
        try:
            ejecutar_ciclo()
            print(f"\n  💤 Esperando {INTERVALO_MINUTOS} minutos...\n")
            time.sleep(INTERVALO_MINUTOS * 60)
        except KeyboardInterrupt:
            print("\n\n  👋 Monitor detenido. ¡Hasta pronto!")
            break
        except Exception as e:
            print(f"\n  ⚠️ Error inesperado: {e}")
            print(f"  🔄 Reintentando en 5 minutos...")
            time.sleep(300)


if __name__ == "__main__":
    main()
