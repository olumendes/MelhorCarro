#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MelhorCarro - Sistema Unificado de Busca de Carros
Feito por Luan Mendes

CORREÇÕES FINAIS:
- Portal sempre visível (azul forte)
- Informações em favoritos visíveis
- Loading overlay centralizado e animado
"""

import flet as ft
import subprocess
import sys
import threading
import json
import os
import time
import re
import unicodedata
from typing import List, Dict, Any, Optional
from urllib.parse import quote

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# Optional imports
try:
    import pandas as pd
except Exception:
    pd = None

try:
    import requests
except Exception:
    requests = None


# ============================================================================
# CONSTANTES E CONFIGURAÇÃO
# ============================================================================

STOP_SIGNAL_PATH = os.path.join(os.getcwd(), "STOP_SIGNAL.txt")
STATE_FILE = os.path.join(os.getcwd(), "app_state.json")

# ============================================================================
# FUNÇÕES UTILITÁRIAS DO SCRAPER
# ============================================================================

def slugify(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip().lower()
    # remove accents
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    # remove invalid chars
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    # spaces to hyphens
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s


def normalize_text(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return s

BODY_TYPE_CODES = {
    normalize_text('Hatch'): '479344',
    normalize_text('Sedã'): '452758',
    normalize_text('Sedan'): '452758',
    normalize_text('SUV'): '452759',
    normalize_text('Pick-Up'): '452756',
    normalize_text('Pickup'): '452756',
    normalize_text('Pick Up'): '452756',
    normalize_text('Minivan'): '452753',
    normalize_text('Monovolume'): '452752',
    normalize_text('Furgão'): '452750',
    normalize_text('Furgao'): '452750',
    normalize_text('Van'): '452755',
    normalize_text('Off-Road'): '452754',
    normalize_text('Off Road'): '452754',
}


def format_int_br(value: int) -> str:
    return f"{value:,}".replace(',', '.')


# Variáveis globais
parar_scraping = False
dados_carros = []
SEMINOVOS_VERBOSE = False
# Reference to currently active Selenium driver (if any) so external stop signal can attempt to close it
current_driver = None
STOP_SIGNAL_PATH = os.path.join(os.getcwd(), "STOP_SIGNAL.txt")

def should_stop():
    global parar_scraping, current_driver
    # External file-based stop signal (written by Node server)
    try:
        if os.path.exists(STOP_SIGNAL_PATH):
            logar("[STOP SIGNAL] Detected stop signal file.")
            parar_scraping = True
            try:
                os.remove(STOP_SIGNAL_PATH)
            except Exception:
                pass
    except Exception:
        pass

    if parar_scraping:
        # Attempt to quit any active Selenium driver immediately
        try:
            if current_driver:
                try:
                    logar("[STOP SIGNAL] Attempting to quit Selenium driver immediately.")
                    current_driver.quit()
                except Exception as e:
                    logar(f"[STOP SIGNAL] Error quitting driver: {e}")
                try:
                    current_driver = None
                except Exception:
                    pass
        except Exception:
            pass

    return parar_scraping

def log_seminovos(msg):
    try:
        if SEMINOVOS_VERBOSE:
            logar(f"[SEMINOVOS-DBG] {msg}")
    except Exception:
        pass

def criar_driver_headless():
    options = Options()
    options.headless = True  # Modo invisível

    # Adicionar argumentos para melhor compatibilidade
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    # Configurações específicas para Windows
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference('useAutomationExtension', False)

    try:
        service = Service()  # Deixa o Selenium encontrar o geckodriver automaticamente
        global current_driver
        driver = webdriver.Firefox(service=service, options=options)
        try:
            current_driver = driver
        except Exception:
            pass
        return driver
    except Exception as e:
        logar(f"❌ Erro ao criar driver Firefox: {e}")
        logar("💡 Verifique se Firefox e GeckoDriver estão instalados")
        raise

def logar(mensagem):
    # Remover emojis para compatibilidade Windows CP1252
    mensagem_limpa = mensagem.encode('ascii', 'ignore').decode('ascii')
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] [SCRAPER] {mensagem_limpa}")

def add_dado(dado):
    # Normalize commonly used fields to improve downstream exports and ranking
    try:
        # Portas: prefer numeric
        portas = None
        for k in ("Portas", "portas"):
            if k in dado and dado[k] is not None and str(dado[k]).strip() != "":
                m = re.search(r"(\d+)", str(dado[k]))
                portas = m.group(1) if m else str(dado[k]).strip()
                break
        if portas:
            dado["Portas"] = portas
            dado["portas"] = portas

        # Quilometragem: normalize to e.g. '34200 km' or keep original
        km_val = None
        for k in ("Quilometragem", "quilometragem", "KM", "km"):
            if k in dado and dado[k]:
                s = str(dado[k])
                m = re.search(r"([\d\.]+)\s*km", s, flags=re.I)
                if m:
                    km_val = m.group(0)
                else:
                    # extract numbers
                    m2 = re.search(r"([\d\.]+)", s)
                    if m2:
                        km_val = m2.group(1) + " km"
                    else:
                        km_val = s.strip()
                break
        if km_val:
            dado["Quilometragem"] = km_val
            dado["quilometragem"] = km_val
            dado["KM"] = km_val
            dado["km"] = km_val

        # Potência do Motor: prefer explicit hp/cv values; if only displacement (e.g., '1.0') keep as potenciaMotor but also set 'motor'
        potencia = None
        for k in ("Potência do Motor", "potenciaMotor", "potencia", "Potencia"):
            if k in dado and dado[k]:
                potencia = str(dado[k]).strip()
                break
        if potencia:
            # if contains hp or cv keep as-is
            if re.search(r"\b(hp|cv)\b", potencia, flags=re.I):
                dado["Potência do Motor"] = potencia
                dado["potenciaMotor"] = potencia
                dado["Potencia"] = potencia
            else:
                # may be '1.0' displacement — set both potenciaMotor and motor fields
                dado["potenciaMotor"] = potencia
                dado["Potencia do Motor"] = potencia
                dado["motor"] = potencia
                dado["Motor"] = potencia

        # Direção / Tipo de Direção normalization
        direc = None
        for k in ("Direção", "direcao", "tipoDirecao", "tipo de direcao", "Tipo de Direção"):
            if k in dado and dado[k]:
                direc = str(dado[k]).strip()
                break
        if direc:
            dado["Direção"] = direc
            dado["direcao"] = direc
            dado["tipoDirecao"] = direc

        # Câmbio normalization (Manual / Automático)
        camb = None
        for k in ("Câmbio", "cambio", "Transmissão", "transmissao"):
            if k in dado and dado[k]:
                camb = str(dado[k]).strip()
                break
        if camb:
            dado["Câmbio"] = camb
            dado["cambio"] = camb

    except Exception as e:
        # best-effort normalization — do not block adding the record
        logar(f"[WARN] Erro ao normalizar dado: {e}")

    dados_carros.append(dado)
    try:
        portal = dado.get('Portal', 'Portal')
        nome = dado.get('Nome do Carro', 'Carro')
        logar(f"[OK] {portal} - {nome}")
        print("EVENT_JSON:" + json.dumps(dado, ensure_ascii=False))
        sys.stdout.flush()
    except Exception:
        pass

def add_dado_improved(dado):
    # Normalize commonly used fields to improve downstream exports and ranking
    try:
        # PORTAS: prefer numeric only; ignore boolean answers like 'Sim'/'Não'
        portas_val = None
        for k in ("Portas", "portas"):
            if k in dado and dado[k] is not None and str(dado[k]).strip() != "":
                s = str(dado[k]).strip()
                m = re.search(r"(\d+)", s)
                if m:
                    portas_val = m.group(1)
                break
        if portas_val:
            dado["Portas"] = portas_val
            dado["portas"] = portas_val

        # QUILOMETRAGEM: normalize to '<number> km'
        km_val = None
        for k in ("Quilometragem", "quilometragem", "KM", "km"):
            if k in dado and dado[k]:
                s = str(dado[k])
                m = re.search(r"([\d\.]+)\s*km", s, flags=re.I)
                if m:
                    km_val = m.group(1).replace('.', '') + " km"
                else:
                    m2 = re.search(r"([\d\.]+)", s)
                    if m2:
                        km_val = m2.group(1).replace('.', '') + " km"
                    else:
                        km_val = s.strip()
                break
        if km_val:
            dado["Quilometragem"] = km_val
            dado["quilometragem"] = km_val
            dado["KM"] = km_val
            dado["km"] = km_val

        # POTÊNCIA / MOTOR: handle horsepower (hp/cv) and displacement (e.g., '1.3') consistently
        potencia = None
        for k in ("Potência do Motor", "potenciaMotor", "potencia", "Potencia"):
            if k in dado and dado[k]:
                potencia = str(dado[k]).strip()
                break
        # if not present, try the Motor field
        if not potencia:
            for k in ("Motor", "motor"):
                if k in dado and dado[k]:
                    potencia = str(dado[k]).strip()
                    break

        if potencia:
            # horsepower explicit
            if re.search(r"\b(hp|cv)\b", potencia, flags=re.I):
                dado["Potência do Motor"] = potencia
                dado["potenciaMotor"] = potencia
                dado["Potencia"] = potencia
            else:
                # numeric-like value -> treat as displacement (motor)
                m_disp = re.search(r"(\d+[\.,]?\d*)", potencia)
                if m_disp:
                    disp = m_disp.group(1).replace(',', '.')
                    dado["motor"] = disp
                    dado["Motor"] = disp
                    # also store in potencia fields for compatibility
                    dado["potenciaMotor"] = disp
                    dado["Potência do Motor"] = disp
                    dado["Potencia"] = disp
                else:
                    # fallback: store raw
                    dado["Potência do Motor"] = potencia
                    dado["potenciaMotor"] = potencia
                    dado["Potencia"] = potencia

        # DIREÇÃO / TIPO DE DIREÇÃO normalization
        direc = None
        for k in ("Direção", "direcao", "tipoDirecao", "tipo de direcao", "Tipo de Direção"):
            if k in dado and dado[k]:
                direc = str(dado[k]).strip()
                break
        if direc:
            dado["Direção"] = direc
            dado["direcao"] = direc
            dado["tipoDirecao"] = direc

        # CÂMBIO normalization (Manual / Automático)
        camb = None
        for k in ("Câmbio", "cambio", "Transmissão", "transmissao"):
            if k in dado and dado[k]:
                camb = str(dado[k]).strip()
                break
        if camb:
            dado["Câmbio"] = camb
            dado["cambio"] = camb

    except Exception as e:
        logar(f"[WARN] Erro ao normalizar dado: {e}")

    # append to global list and emit event like original add_dado
    dados_carros.append(dado)
    try:
        portal = dado.get('Portal', 'Portal')
        nome = dado.get('Nome do Carro', 'Carro')
        logar(f"[OK] {portal} - {nome}")
        print("EVENT_JSON:" + json.dumps(dado, ensure_ascii=False))
        sys.stdout.flush()
    except Exception:
        pass

# keep the old add_dado name but point to improved function so other code continues to call add_dado
add_dado = add_dado_improved

def fetch_via_zenrows(page_url: str, api_key: str, waits=(3000,6000,9000,12000)) -> str:
    """Fetch page via ZenRows and return HTML text. Returns empty string on failure."""
    try:
        from urllib.request import urlopen
    except Exception:
        return ''
    for w in waits:
        try:
            zen_url = (
                "https://api.zenrows.com/v1/?" +
                f"url={quote(page_url, safe='')}&apikey={quote(api_key)}&js_render=true&premium_proxy=true&antibot=true&wait={w}"
            )
            with urlopen(zen_url, timeout=90) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')
            # quick sanity probe
            if html_text and len(html_text) > 100:
                return html_text
        except Exception as e:
            logar(f"[ZenRows] fetch failed for {page_url} wait={w}: {e}")
            continue
    return ''


def extract_olx_details_from_html(html_text: str, forbidden_words: list) -> dict:
    details = {
        "ano": "",
        "potenciaMotor": "",
        "portas": "",
        "direcao": "",
        "cambio": "",
        "tipoDirecao": "",
        "combustivel": "",
        "quilometragem": "",
        "descricao": "",
        "palavrasProibidas": []
    }
    try:
        body = html_text or ''
        # Year
        m = re.search(r"\b(19|20)\d{2}\b", body)
        if m:
            details['ano'] = m.group(0)
        # Quilometragem
        m = re.search(r"([\d\.]+)\s*km", body, flags=re.I)
        if m:
            details['quilometragem'] = m.group(1).replace('.', '') + ' km'
        # Potencia (hp/cv)
        m = re.search(r"pot[eê]ncia[:\s\n]*([\d\.,]+\s*(hp|cv)?)", body, flags=re.I)
        if m:
            details['potenciaMotor'] = m.group(1).strip()
        # Portas
        m = re.search(r"\bportas?\b[:\s\n]*([\d]+)", body, flags=re.I)
        if m:
            details['portas'] = m.group(1)
        # Direcao / Tipo de direcao
        m = re.search(r"\b(direc(?:ç|c)ao|direção|tipo de direção|tipo de direcao)[:\s\n]*([A-Za-zÀ-ú0-9 ]{2,30})", body, flags=re.I)
        if m:
            details['direcao'] = m.group(2).strip()
            details['tipoDirecao'] = m.group(2).strip()
        # Cambio
        m = re.search(r"\b(c[âa]mbi[oõ]s?|transmiss(?:ã|a)o)[:\s\n]*([A-Za-z0-9\s-]{3,30})", body, flags=re.I)
        if m:
            details['cambio'] = m.group(2).strip()
        # Combustivel
        m = re.search(r"\bcombust[ií]vel[:\s\n]*([A-Za-z0-9\s/]{3,30})", body, flags=re.I)
        if m:
            details['combustivel'] = m.group(1).strip()
        # Description: meta description or og:description
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if not m:
            m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if m:
            desc = re.sub(r"\s+", " ", m.group(1)).strip()
            details['descricao'] = desc
        else:
            # fallback: take first large paragraph
            m = re.search(r'<p[^>]{0,120}>([^<]{50,400})</p>', html_text, flags=re.I)
            if m:
                details['descricao'] = re.sub(r"\s+", " ", m.group(1)).strip()
        # forbidden words
        desc_norm = normalize_text(details.get('descricao',''))
        for w in forbidden_words or []:
            if normalize_text(w.strip()) and normalize_text(w.strip()) in desc_norm:
                details['palavrasProibidas'].append(w.strip())
    except Exception as e:
        logar(f"[OLX][HTML] erro ao extrair detalhes do html: {e}")
    return details


def extract_details_seminovos_from_html(html_text: str, forbidden_words: list) -> dict:
    details = {
        "quilometragem": "",
        "cambio": "",
        "ano": "",
        "portas": "",
        "combustivel": "",
        "cor": "",
        "descricao": "",
        "motor": "",
        "potenciaMotor": "",
        "palavrasProibidas": []
    }
    try:
        body = html_text or ''
        # attempt structured block extraction: look for campo/valor pairs
        pairs = re.findall(r'<(?:div|li|span)[^>]+class=["\'][^"\']*(campo|label)[^"\']*["\'][^>]*>([^<]+)</(?:div|li|span)>\s*<(?:div|span)[^>]+class=["\'][^"\']*(valor|value)[^"\']*["\'][^>]*>([^<]+)</(?:div|span)>', body, flags=re.I)
        if pairs:
            for p in pairs:
                key = normalize_text(p[1])
                val = p[3].strip()
                if 'quilometr' in key:
                    m = re.search(r'([\d\.]+)\s*km', val, flags=re.I)
                    details['quilometragem'] = (m.group(1).replace('.','') + ' km') if m else val
                elif 'cambi' in key or 'transmiss' in key:
                    details['cambio'] = val
                elif 'ano' in key:
                    details['ano'] = val
                elif 'porta' in key:
                    m = re.search(r'(\d+)', val)
                    if m: details['portas'] = m.group(1)
                elif 'combust' in key:
                    details['combustivel'] = val
                elif 'cor' in key:
                    details['cor'] = val
        # fallback regex extractions
        if not details['quilometragem']:
            m = re.search(r'([\d\.]+)\s*km', body, flags=re.I)
            if m:
                details['quilometragem'] = m.group(1).replace('.', '') + ' km'
        if not details['ano']:
            m = re.search(r"\b(19|20)\d{2}\b", body)
            if m:
                details['ano'] = m.group(0)
        if not details['portas']:
            m = re.search(r"\bportas?\b[:\s\n]*([\d]+)", body, flags=re.I)
            if m:
                details['portas'] = m.group(1)
        if not details['cambio']:
            m = re.search(r"\b(c[âa]mbi[oõ]|transmiss)[:\s\n]*([A-Za-z0-9\s-]{3,30})", body, flags=re.I)
            if m:
                details['cambio'] = m.group(2).strip()
        # description
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if not m:
            m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if m:
            details['descricao'] = re.sub(r"\s+", " ", m.group(1)).strip()
        else:
            m = re.search(r'<p[^>]{0,120}>([^<]{50,400})</p>', html_text, flags=re.I)
            if m:
                details['descricao'] = re.sub(r"\s+", " ", m.group(1)).strip()
        # forbidden words
        desc_norm = normalize_text(details.get('descricao',''))
        for w in forbidden_words or []:
            if normalize_text(w.strip()) and normalize_text(w.strip()) in desc_norm:
                details['palavrasProibidas'].append(w.strip())
    except Exception as e:
        log_seminovos(f"[SEMINOVOS][HTML] erro ao extrair detalhes: {e}")
    return details


def scraping_olx(filtros):
    try:
        # If ZenRows key provided, use it to fetch listing and detail pages (avoid Selenium)
        api_key = (filtros.get('zenrows_api_key') or '').strip() or os.getenv('ZENROWS_API_KEY') or ''
        if api_key:
            logar("[OLX][ZenRows] Usando ZenRows para buscar listagens e detalhes")
            forbidden_words = filtros.get("forbiddenWords", []) or []
            # Build base URL similar to Selenium path
            cidade = (filtros.get("cidade") or "belo-horizonte").strip().lower().replace(" ", "-")
            cidade = ''.join(ch for ch in cidade if ch.isalnum() or ch == '-')
            sub_cidade = f"/grande-belo-horizonte/{cidade}" if cidade and cidade != "belo-horizonte" else ""
            base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/estado-mg/belo-horizonte-e-regiao{sub_cidade}"

            modelo_slug = slugify(filtros.get("modelo") or '')
            carroceria_slug = slugify(filtros.get("carroceria") or '')
            if not filtros.get("marca") and carroceria_slug:
                base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
            elif filtros.get("marca"):
                marca_slug = slugify(filtros.get("marca"))
                if modelo_slug and carroceria_slug:
                    base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{modelo_slug}/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
                elif modelo_slug:
                    base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{modelo_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
                elif carroceria_slug:
                    base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
                else:
                    base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"

            filtros_url = []
            if filtros.get("precoMin") or filtros.get("preco_min"):
                filtros_url.append(f"ps={filtros.get('precoMin', filtros.get('preco_min'))}")
            if filtros.get("precoMax") or filtros.get("preco_max"):
                filtros_url.append(f"pe={filtros.get('precoMax', filtros.get('preco_max'))}")
            if filtros.get("anoMin") or filtros.get("ano_min"):
                filtros_url.append(f"rs={filtros.get('anoMin', filtros.get('ano_min'))}")
            if filtros.get("anoMax") or filtros.get("ano_max"):
                filtros_url.append(f"re={filtros.get('anoMax', filtros.get('ano_max'))}")
            if filtros.get('kmMin') or filtros.get('km_min'):
                filtros_url.append(f"mi={filtros.get('kmMin', filtros.get('km_min'))}")
            if filtros.get('kmMax') or filtros.get('km_max'):
                filtros_url.append(f"me={filtros.get('kmMax', filtros.get('km_max'))}")

            url = base
            if filtros_url:
                url += "?" + "&".join(filtros_url)

            collected_links = []
            for page in range(1, 6):
                page_url = url + ("&" if "?" in url else "?") + f"page={page}"
                html_text = fetch_via_zenrows(page_url, api_key)
                if not html_text:
                    continue
                # try to find OLX ad links
                matches = re.findall(r'href=["\'](https?://www\.olx\.com\.br/[^"]+)["\']', html_text, flags=re.I)
                for m in matches:
                    if m and m not in collected_links and '/autos-e-pecas/' in m:
                        collected_links.append(m.split('?')[0])
                if len(collected_links) >= 200:
                    break

            forbidden = filtros.get('forbiddenWords') or []
            for link in collected_links:
                if should_stop():
                    break
                try:
                    d_html = fetch_via_zenrows(link, api_key)
                    if not d_html:
                        continue
                    details = extract_olx_details_from_html(d_html, forbidden)
                    nome = ''
                    mn = re.search(r'<h1[^>]*>([^<]+)</h1>', d_html, flags=re.I)
                    if mn:
                        nome = mn.group(1).strip()
                    preco = ''
                    mp = re.search(r'(?:R\$|R\s?\$)\s*[\d\.,]+', d_html)
                    if mp:
                        preco = mp.group(0)
                    imagem = ''
                    mg = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', d_html, flags=re.I)
                    if mg:
                        imagem = mg.group(1)
                    car_data = {
                        'Nome do Carro': nome or 'OLX Car',
                        'Valor': preco,
                        'KM': details.get('quilometragem',''),
                        'Localização': '',
                        'Imagem': imagem,
                        'Link': link,
                        'Portal': 'OLX'
                    }
                    car_data.update({
                        "Ano": details.get("ano", ''),
                        "Motor": details.get("motor", ''),
                        "Potência do Motor": details.get("potenciaMotor", ''),
                        "Portas": details.get("portas", ''),
                        "Direção": details.get("direcao", ''),
                        "Câmbio": details.get("cambio", ''),
                        "Tipo de Direção": details.get("tipoDirecao", ''),
                        "Combustível": details.get("combustivel", ''),
                        "Quilometragem": details.get("quilometragem", ''),
                        "Descrição": details.get("descricao", ''),
                        "Palavras Proibidas": details.get("palavrasProibidas", [])
                    })
                    add_dado(car_data)
                except Exception as e:
                    logar(f"[OLX][ZenRows] Erro processando link {link}: {e}")
            return

        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        global current_driver
        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            current_driver = driver
        except Exception:
            pass

        forbidden_words = filtros.get("forbiddenWords", []) or []
        capture_details = filtros.get("capture_details", True)

        # Gerar URL da OLX baseada nos filtros
        cidade = (filtros.get("cidade") or "belo-horizonte").strip().lower().replace(" ", "-")
        # limpar caracteres especiais para slug
        cidade = ''.join(ch for ch in cidade if ch.isalnum() or ch == '-')
        sub_cidade = f"/grande-belo-horizonte/{cidade}" if cidade and cidade != "belo-horizonte" else ""

        base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
        filtros_url = []

        modelo_slug = slugify(filtros.get("modelo") or '')
        carroceria_slug = slugify(filtros.get("carroceria") or '')

        # If no brand provided but carroceria is, use /carroceria/ path
        if not filtros.get("marca") and carroceria_slug:
            base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
        elif filtros.get("marca"):
            marca_slug = slugify(filtros.get("marca"))
            if modelo_slug and carroceria_slug:
                base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{modelo_slug}/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
            elif modelo_slug:
                base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{modelo_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
            elif carroceria_slug:
                base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/{carroceria_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"
            else:
                base = f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/{marca_slug}/estado-mg/belo-horizonte-e-regiao{sub_cidade}"

        if filtros.get("precoMin") or filtros.get("preco_min"):
            filtros_url.append(f"ps={filtros.get('precoMin', filtros.get('preco_min'))}")
        if filtros.get("precoMax") or filtros.get("preco_max"):
            filtros_url.append(f"pe={filtros.get('precoMax', filtros.get('preco_max'))}")
        if filtros.get("anoMin") or filtros.get("ano_min"):
            filtros_url.append(f"rs={filtros.get('anoMin', filtros.get('ano_min'))}")
        if filtros.get("anoMax") or filtros.get("ano_max"):
            filtros_url.append(f"re={filtros.get('anoMax', filtros.get('ano_max'))}")
        if filtros.get('kmMin') or filtros.get('km_min'):
            filtros_url.append(f"mi={filtros.get('kmMin', filtros.get('km_min'))}")
        if filtros.get('kmMax') or filtros.get('km_max'):
            filtros_url.append(f"me={filtros.get('kmMax', filtros.get('km_max'))}")

        url = base
        if filtros_url:
            url += "?" + "&".join(filtros_url)

        logar(f"[OLX] Acessando: {url}")
        driver.get(url)
        time.sleep(2)

        # Aplicar filtros adicionais que não s��o passados via URL na OLX
        try:
            # Filtro de portas
            if filtros.get("portas"):
                portas_valor = filtros.get("portas", "").strip()
                if portas_valor:
                    try:
                        # Procura por fieldset com legend contendo "Portas"
                        fieldsets = driver.find_elements(By.CSS_SELECTOR, 'fieldset')
                        for fieldset in fieldsets:
                            try:
                                legend = fieldset.find_element(By.TAG_NAME, 'legend')
                                if 'portas' in legend.text.lower() or 'doors' in legend.text.lower():
                                    checkboxes = fieldset.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
                                    for checkbox in checkboxes:
                                        try:
                                            parent = checkbox.find_element(By.XPATH, "ancestor::label")
                                            label_text = parent.text.lower()
                                            if portas_valor.lower() in label_text:
                                                if not checkbox.is_selected():
                                                    driver.execute_script("arguments[0].click();", checkbox)
                                                    time.sleep(0.5)
                                                logar(f"[OLX] Filtro Portas aplicado: {portas_valor}")
                                                break
                                        except Exception:
                                            continue
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        logar(f"[OLX] Aviso: Nao foi possivel aplicar filtro de portas: {e}")

            # Filtro de combustível
            if filtros.get("combustivel"):
                combustivel_valor = filtros.get("combustivel", "").strip().lower()
                try:
                    fieldsets = driver.find_elements(By.CSS_SELECTOR, 'fieldset')
                    for fieldset in fieldsets:
                        try:
                            legend = fieldset.find_element(By.TAG_NAME, 'legend')
                            if 'combustível' in legend.text.lower() or 'combustivel' in legend.text.lower():
                                checkboxes = fieldset.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
                                for checkbox in checkboxes:
                                    try:
                                        parent = checkbox.find_element(By.XPATH, "ancestor::label")
                                        label_text = parent.text.lower()
                                        if combustivel_valor in label_text:
                                            if not checkbox.is_selected():
                                                driver.execute_script("arguments[0].click();", checkbox)
                                                time.sleep(0.5)
                                            logar(f"[OLX] Filtro Combustível aplicado: {combustivel_valor}")
                                            break
                                    except Exception:
                                        continue
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[OLX] Aviso: Nao foi possivel aplicar filtro de combustível: {e}")

            # Filtro de transmissão
            if filtros.get("transmissao"):
                transmissao_valor = filtros.get("transmissao", "").strip().lower()
                try:
                    fieldsets = driver.find_elements(By.CSS_SELECTOR, 'fieldset')
                    for fieldset in fieldsets:
                        try:
                            legend = fieldset.find_element(By.TAG_NAME, 'legend')
                            if 'transmissão' in legend.text.lower() or 'transmissao' in legend.text.lower():
                                checkboxes = fieldset.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
                                for checkbox in checkboxes:
                                    try:
                                        parent = checkbox.find_element(By.XPATH, "ancestor::label")
                                        label_text = parent.text.lower()
                                        if transmissao_valor in label_text:
                                            if not checkbox.is_selected():
                                                driver.execute_script("arguments[0].click();", checkbox)
                                                time.sleep(0.5)
                                            logar(f"[OLX] Filtro Transmissão aplicado: {transmissao_valor}")
                                            break
                                    except Exception:
                                        continue
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[OLX] Aviso: Nao foi possivel aplicar filtro de transmissão: {e}")

            # Filtro de cor
            if filtros.get("cor"):
                cor_valor = filtros.get("cor", "").strip().lower()
                try:
                    fieldsets = driver.find_elements(By.CSS_SELECTOR, 'fieldset')
                    for fieldset in fieldsets:
                        try:
                            legend = fieldset.find_element(By.TAG_NAME, 'legend')
                            if 'cor' in legend.text.lower() or 'color' in legend.text.lower():
                                checkboxes = fieldset.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
                                for checkbox in checkboxes:
                                    try:
                                        parent = checkbox.find_element(By.XPATH, "ancestor::label")
                                        label_text = parent.text.lower()
                                        if cor_valor in label_text:
                                            if not checkbox.is_selected():
                                                driver.execute_script("arguments[0].click();", checkbox)
                                                time.sleep(0.5)
                                            logar(f"[OLX] Filtro Cor aplicado: {cor_valor}")
                                            break
                                    except Exception:
                                        continue
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[OLX] Aviso: Nao foi possivel aplicar filtro de cor: {e}")

            time.sleep(1)
        except Exception as e:
            logar(f"[OLX] Aviso: Erro ao aplicar filtros dinamicos: {e}")

        pagina = 1
        while not should_stop() and pagina <= 5:  # Limitar a 5 páginas para não demorar muito
            logar(f"[OLX] Pagina {pagina}")
            try:
                # wait for any ad-like element to appear
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'section.olx-adcard, li[data-lurker-listitemid], a[data-testid="ad-card"], .sc-1fcmfeb-2')))
            except:
                logar("[OLX] Nenhum anuncio encontrado ou pagina nao carregou (fallback check). Tentando seletores alternativos.")

            # Try multiple possible selectors to find ads (fallbacks for markup changes)
            anuncios = []
            try:
                anuncios = driver.find_elements(By.CSS_SELECTOR, 'section.olx-adcard')
            except:
                anuncios = []

            if not anuncios:
                try:
                    anuncios = driver.find_elements(By.CSS_SELECTOR, 'li[data-lurker-listitemid], a[data-testid="ad-card"], .sc-1fcmfeb-2, div.sc-1fcmfeb-2')
                except:
                    anuncios = []

            # Final fallback: any anchor inside main list that looks like an ad
            if not anuncios:
                try:
                    anchors = driver.find_elements(By.CSS_SELECTOR, 'a[href^="https://www.olx.com.br/ans/"], a[href*="/anuncio/"]')
                    anuncios = anchors[:30]
                except:
                    anuncios = []

            # Filter out recommendation/carousel items (e.g., 'Baseado na sua navegação' or Rec-Gallery carousels)
            def is_recommendation(el):
                try:
                    # ancestor with explicit Rec-Gallery data-component
                    el.find_element(By.XPATH, "ancestor::*[@data-component='Rec-Gallery']")
                    return True
                except Exception:
                    pass
                try:
                    # ancestor containing a 'Baseado na sua navegação' header
                    el.find_element(By.XPATH, "ancestor::*[descendant::h2[contains(normalize-space(.),'Baseado na sua navegação')]]")
                    return True
                except Exception:
                    pass
                try:
                    # ancestor with classes that usually indicate recommendation/gallery blocks
                    el.find_element(By.XPATH, "ancestor::*[contains(@class,'RecommendationGallery') or contains(@class,'Recommendation')]")
                    return True
                except Exception:
                    pass
                return False

            filtered = []
            for a in anuncios:
                try:
                    if is_recommendation(a):
                        continue
                except Exception:
                    pass
                filtered.append(a)
            anuncios = filtered

            if not anuncios:
                logar("[OLX] Nenhum anuncio detectado com seletores conhecidos. Salvando snapshot para analise.")
                try:
                    with open('olx_page_snapshot.html', 'w', encoding='utf-8') as f:
                        f.write(driver.page_source[:100000])
                except:
                    pass
                break

            # Store the current listing page URL before processing individual cars
            listing_page_url = driver.current_url

            # First pass: collect car data from list view
            cars_to_process = []
            for anuncio in anuncios:
                if should_stop():
                    break
                try:
                    # Attempt to extract name/value/image from ad container or anchor
                    nome = ""
                    try:
                        nome = anuncio.find_element(By.CSS_SELECTOR, 'h2').text.strip()
                    except:
                        try:
                            nome = anuncio.text.split('\n')[0].strip()
                        except:
                            nome = ''
                    imagem = ''
                    try:
                        img_el = anuncio.find_element(By.CSS_SELECTOR, 'picture img')
                        imagem = img_el.get_attribute('src')
                    except:
                        try:
                            img_el = anuncio.find_element(By.CSS_SELECTOR, 'img')
                            imagem = img_el.get_attribute('src')
                        except:
                            imagem = ''
                    valor = ''
                    price_selectors = [
                        'span[data-testid="ad-price"]',
                        'div[data-testid="ad-price"]',
                        'span[data-testid="listing-card-price"]',
                        '.olx-adcard__price',
                        '.sc-1fcmfeb-4',
                        '.ad-card__price',
                        '.price',
                    ]
                    for selector in price_selectors:
                        try:
                            price_el = anuncio.find_element(By.CSS_SELECTOR, selector)
                            text = price_el.text.strip()
                            if text:
                                valor = text
                                break
                        except Exception:
                            continue
                    if not valor:
                        try:
                            raw_text = anuncio.text
                            if raw_text:
                                for line in [line.strip() for line in raw_text.splitlines() if line.strip()]:
                                    cleaned = line.replace('\u00a0', ' ')
                                    if 'R$' in cleaned:
                                        valor = cleaned
                                        break
                        except Exception:
                            valor = ''
                    km = ''
                    motor = ''
                    try:
                        detalhes = anuncio.find_elements(By.CSS_SELECTOR, '.olx-adcard__detail')
                        if detalhes:
                            km = detalhes[0].get_attribute('aria-label') or ''
                            # Try to extract motor from other detail elements
                            for detalhe in detalhes:
                                aria_label = detalhe.get_attribute('aria-label') or ''
                                if 'motor' in aria_label.lower():
                                    motor = aria_label.replace('Motor ', '').strip()
                                    break
                    except:
                        km = ''
                    local = ''
                    try:
                        local = anuncio.find_element(By.CSS_SELECTOR, '.olx-adcard__location').text.strip()
                    except:
                        local = ''
                    link = ''
                    try:
                        link = anuncio.get_attribute('href') or anuncio.find_element(By.CSS_SELECTOR, 'a').get_attribute('href')
                    except:
                        link = ''

                    # Aplicar filtros adicionais locais (modelo, km, carroceria) usando normalização
                    modelo = normalize_text(filtros.get('modelo') or '')
                    nome_norm = normalize_text(nome)
                    if modelo and modelo not in nome_norm:
                        continue
                    carroceria = normalize_text(filtros.get('carroceria') or '')
                    if carroceria and carroceria not in nome_norm:
                        continue
                    try:
                        km_numbers = ''.join(ch for ch in km if ch.isdigit())
                        if km_numbers:
                            vkm = int(km_numbers)
                            kmin = int(filtros.get('kmMin') or filtros.get('km_min') or 0)
                            kmax = int(filtros.get('kmMax') or filtros.get('km_max') or 0) or 10**9
                            if vkm < kmin or vkm > kmax:
                                continue
                    except Exception:
                        pass

                    car_data = {
                        "Nome do Carro": nome,
                        "Valor": valor,
                        "KM": km,
                        "Motor": motor,
                        "Localização": local,
                        "Imagem": imagem,
                        "Link": link,
                        "Portal": "OLX"
                    }

                    # Store car data with its link for detailed processing later
                    if link:
                        cars_to_process.append((car_data, link))
                    else:
                        add_dado(car_data)
                        logar(f"[OK] OLX - {nome}")

                except Exception:
                    continue

            # Second pass: extract detailed information for each car (avoiding stale element references)
            for car_data, link in cars_to_process:
                if should_stop():
                    break
                try:
                    if capture_details:
                        try:
                            details = extract_olx_details(driver, link, forbidden_words, listing_page_url)
                            car_data.update({
                                "Ano": details["ano"],
                                "Motor": details.get("motor", "") ,
                                "Potência do Motor": details["potenciaMotor"],
                                "Portas": details["portas"],
                                "Direção": details["direcao"],
                                "Câmbio": details["cambio"],
                                "Tipo de Direção": details["tipoDirecao"],
                                "Combustível": details["combustivel"],
                                "Quilometragem": details["quilometragem"],
                                "Descrição": details["descricao"],
                                "Palavras Proibidas": details["palavrasProibidas"]
                            })
                        except Exception as e:
                            logar(f"[OLX] Erro ao capturar detalhes: {e}")

                    add_dado(car_data)
                    logar(f"[OK] OLX - {car_data['Nome do Carro']}")
                except Exception as e:
                    logar(f"[OLX] Erro ao processar carro detalhado: {e}")
                    continue

            # Próxima página: try rel=next or aria-label/title fallbacks
            try:
                next_link = None
                advanced = False

                # Prefer anchors with rel="next"
                try:
                    el = driver.find_element(By.CSS_SELECTOR, 'a[rel="next"]')
                    href = el.get_attribute('href')
                    if href:
                        next_link = href
                        advanced = True
                except Exception:
                    pass

                # aria-label / title fallbacks
                if not advanced:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, 'a[aria-label*="Próxima"], a[title*="Próxima"], a[aria-label*="Avançar"], a[title*="Avançar"]')
                        href = el.get_attribute('href')
                        if href:
                            next_link = href
                            advanced = True
                    except Exception:
                        pass

                # Inspect anchors by text content
                if not advanced:
                    try:
                        anchors = driver.find_elements(By.TAG_NAME, 'a')
                        for anchor in anchors:
                            label = normalize_text(anchor.text or '')
                            if not label:
                                continue
                            if 'proxima' in label or 'proxima pagina' in label or 'avancar' in label or 'next' in label:
                                href = anchor.get_attribute('href')
                                if href:
                                    next_link = href
                                    advanced = True
                                    break
                    except Exception:
                        pass

                # Button element wrapping the next-page anchor
                if not advanced:
                    try:
                        button = driver.find_element(By.XPATH, "//button[contains(@class,'olx-core-button')][descendant::a]")
                        anchor = button.find_element(By.TAG_NAME, 'a')
                        label = normalize_text(anchor.text or '')
                        if label and ('proxima' in label or 'proxima pagina' in label or 'avancar' in label or 'next' in label):
                            href = anchor.get_attribute('href')
                            if href:
                                next_link = href
                                advanced = True
                    except Exception:
                        pass

                if advanced and next_link:
                    driver.get(next_link)
                    pagina += 1
                    time.sleep(2)
                else:
                    break
            except Exception:
                break

        driver.quit()

    except Exception as e:
        logar(f"[ERRO] OLX - Erro: {str(e)}")

def extract_olx_details(driver, url: str, forbidden_words: list, current_listing_url: str = None) -> dict:
    """Extract detailed information from OLX car detail page"""
    details = {
        "ano": "",
        "potenciaMotor": "",
        "portas": "",
        "direcao": "",
        "cambio": "",
        "tipoDirecao": "",
        "combustivel": "",
        "quilometragem": "",
        "descricao": "",
        "palavrasProibidas": []
    }

    # keep track of original window handle so we can return to it without reloading
    original_handle = driver.current_window_handle
    used_new_tab = False

    try:
        # Open detail in a new tab to avoid losing listing context
        existing_handles = set(driver.window_handles)
        try:
            driver.execute_script("window.open(arguments[0], '_blank');", url)
            WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(existing_handles))
            new_handles = [h for h in driver.window_handles if h not in existing_handles]
            if new_handles:
                driver.switch_to.window(new_handles[0])
                used_new_tab = True
            else:
                driver.get(url)
                used_new_tab = False
        except Exception:
            # Fallback to navigating in same tab
            driver.get(url)
            used_new_tab = False

        # Wait for common detail selectors (try several fallbacks)
        detail_wait_selectors = [
            "[data-section='description']",
            "[data-testid='ad-price']",
            "section[data-testid='ad']",
            "main",
            "div.sc-1fcmfeb-2",
            "[id*='ad']",
        ]
        waited = False
        for sel in detail_wait_selectors:
            try:
                WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                waited = True
                break
            except Exception:
                continue
        if not waited:
            # final short sleep as last resort
            time.sleep(1.5)

        # Extract year from possible places
        try:
            page_text = driver.find_element(By.TAG_NAME, 'body').text
            ymatch = re.search(r"\b(19|20)\d{2}\b", page_text)
            if ymatch:
                details["ano"] = ymatch.group(0)
        except Exception:
            pass

        # Try structured detail extraction first
        try:
            # Many OLX pages render attribute pairs with an 'overline' label and a sibling value
            try:
                label_nodes = driver.find_elements(By.CSS_SELECTOR, "[data-variant='overline']")
            except Exception:
                label_nodes = []

            for node in label_nodes:
                try:
                    label_text = (node.text or '').lower().strip()

                    # immediate parent should contain the value alongside the label
                    parent = None
                    try:
                        parent = node.find_element(By.XPATH, '..')
                    except Exception:
                        parent = None

                    value_text = ''
                    if parent:
                        # 1) prefer anchor texts in the same parent (common for OLX values)
                        try:
                            a = parent.find_element(By.TAG_NAME, 'a')
                            if a and (a.text or '').strip() and (a.text or '').strip() != node.text:
                                value_text = (a.text or '').strip()
                        except Exception:
                            pass

                        # 2) prefer span elements that are not the label (exclude data-variant overline)
                        if not value_text:
                            try:
                                spans = parent.find_elements(By.TAG_NAME, 'span')
                                # choose the span with the most text that is not the label
                                best = ''
                                for sp in spans:
                                    try:
                                        if sp.get_attribute('data-variant') == 'overline':
                                            continue
                                        txt = (sp.text or '').strip()
                                        if txt and txt != node.text and len(txt) > len(best):
                                            best = txt
                                    except Exception:
                                        continue
                                if best:
                                    value_text = best
                            except Exception:
                                pass

                        # 3) fallback to any text inside parent excluding the label text (trim and dedupe)
                        if not value_text:
                            try:
                                full = parent.text or ''
                                label_raw = (node.text or '').strip()
                                # remove the first occurrence of the label to avoid collisions
                                full_minus_label = full.replace(label_raw, '', 1).strip()
                                # if the remaining text is short and looks like a boolean ('sim'/'não'), keep it
                                value_text = full_minus_label
                            except Exception:
                                value_text = ''

                    # 4) as a last resort, check the immediate following sibling element
                    if not value_text:
                        try:
                            sib = node.find_element(By.XPATH, 'following-sibling::*')
                            v = (sib.text or '').strip()
                            if v and v != node.text:
                                value_text = v
                        except Exception:
                            value_text = ''

                    if not label_text:
                        continue

                    # Normalize value_text
                    value_text = (value_text or '').strip()

                    # Map known labels (use word boundaries to avoid 'porta copos')
                    # PORTAS: only store when a numeric value is found
                    if re.search(r"\bportas?\b", label_text) and not re.search(r"porta copos|porta-copos|copos", label_text):
                        m = re.search(r"(\d+)", value_text)
                        if m:
                            details['portas'] = m.group(1)
                        # else: skip storing non-numeric answers like 'Sim' or 'Não'

                    # ANO
                    elif re.search(r"\b(ano|ano de)\b", label_text):
                        y = re.search(r"\b(19|20)\d{2}\b", value_text)
                        details['ano'] = y.group(0) if y else value_text

                    # QUILOMETRAGEM
                    elif re.search(r"\bquil[oô]m[eê]tr|quilometr", label_text):
                        m = re.search(r"([\d\.]+)\s*km", value_text.replace('\u00a0',' '), flags=re.I)
                        if m:
                            details['quilometragem'] = m.group(0).strip()
                        else:
                            # sometimes OLX shows plain numbers; accept them and normalize later
                            digits = re.search(r"([\d\.]+)", value_text)
                            details['quilometragem'] = (digits.group(1) + ' km') if digits else value_text

                    # POTENCIA / MOTOR: be careful - OLX sometimes uses 'Potência do motor' to show displacement
                    elif 'potência do motor' in label_text or 'potencia do motor' in label_text or re.search(r"\bpot[eê]ncia\b", label_text):
                        vt = value_text
                        # if contains hp or cv -> it's horsepower
                        if re.search(r"\b(hp|cv)\b", vt, flags=re.I):
                            details['potenciaMotor'] = vt
                        else:
                            # numeric single value like '1.3' or '1,3' is likely displacement - store to motor and also keep potenciaMotor
                            mdisp = re.search(r"^(\d+[\.,]?\d*)$", vt)
                            if mdisp:
                                disp = mdisp.group(1).replace(',', '.')
                                details['motor'] = disp
                                details['potenciaMotor'] = disp
                            else:
                                # ambiguous string - keep raw under potenciaMotor
                                details['potenciaMotor'] = vt

                    # DIREÇÃO / TIPO DE DIREÇÃO
                    elif re.search(r"\bdirec(?:ção|ao|cao|ao)\b", label_text) or 'tipo de direção' in label_text or 'tipo de direcao' in label_text:
                        details['direcao'] = value_text
                        details['tipoDirecao'] = value_text

                    # CÂMBIO / TRANSMISSÃO
                    elif re.search(r"\bc[âa]mbi?o\b", label_text) or re.search(r"\btransmiss(?:ão|ao)\b", label_text):
                        details['cambio'] = value_text

                    # COMBUSTÍVEL
                    elif re.search(r"\bcombust[ií]vel\b", label_text) or 'combustivel' in label_text:
                        details['combustivel'] = value_text

                except Exception:
                    continue

            # If some fields still missing, fallback to body regex but use strict word boundaries
            body_text = driver.find_element(By.TAG_NAME, 'body').text

            if not details['portas']:
                m = re.search(r"\bportas?\b[:\s\n]*([\d]+)", body_text, flags=re.I)
                if m:
                    details['portas'] = m.group(1)

            if not details['ano']:
                m = re.search(r"\b(19|20)\d{2}\b", body_text)
                if m:
                    details['ano'] = m.group(0)

            if not details['quilometragem']:
                m = re.search(r"([\d\.]+)\s*km", body_text, flags=re.I)
                if m:
                    details['quilometragem'] = m.group(0)

            if not details['potenciaMotor']:
                # attempt to find 'Potência' followed by hp or number
                m = re.search(r"pot[eê]ncia[:\s\n]*([\d\.,]+\s*(hp|cv)?)", body_text, flags=re.I)
                if m:
                    details['potenciaMotor'] = m.group(1).strip()

        except Exception as e:
            logar(f"[OLX] Aviso ao extrair detalhe estruturado: {e}")

        # Extract description for forbidden words check (robust fallback)
        try:
            description_text = ''
            try:
                desc_section = driver.find_element(By.CSS_SELECTOR, "[data-section='description']")
                description_text = desc_section.text.strip()
            except Exception:
                try:
                    # common class patterns
                    desc_section = driver.find_element(By.CSS_SELECTOR, "div[itemprop='description']")
                    description_text = desc_section.text.strip()
                except Exception:
                    # Last resort: take large body text slice
                    description_text = driver.find_element(By.TAG_NAME, 'body').text[:10000]

            details["descricao"] = description_text
            desc_normalized = normalize_text(description_text)
            for word in forbidden_words:
                word_normalized = normalize_text(word.strip())
                if word_normalized and word_normalized in desc_normalized:
                    details["palavrasProibidas"].append(word.strip())
        except Exception as e:
            logar(f"[OLX] Aviso ao extrair descrição: {e}")

    except Exception as e:
        logar(f"[OLX] Erro ao extrair detalhes da página: {e}")

    finally:
        # Close detail tab and return to the listing page without reloading unnecessarily
        try:
            if used_new_tab:
                try:
                    driver.close()
                except Exception:
                    try:
                        driver.execute_script("window.close();")
                    except Exception:
                        pass
                time.sleep(0.3)
                try:
                    if original_handle and original_handle in driver.window_handles:
                        driver.switch_to.window(original_handle)
                    elif driver.window_handles:
                        driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass
            else:
                # We navigated in the same tab; try to return to listing without forcing reload when possible
                try:
                    if current_listing_url:
                        if driver.current_url != current_listing_url:
                            driver.get(current_listing_url)
                    else:
                        try:
                            driver.back()
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception as e:
            logar(f"[OLX] Aviso ao retornar à página anterior: {e}")

    return details

def scraping_webmotors(filtros):
    try:
        # Inputs
        cidade_uf_in = filtros.get("cidadeUf") or filtros.get("cidade_uf") or "mg-belo-horizonte"
        cidade_nome = (filtros.get("cidade") or "").strip()
        ano_min = filtros.get("anoMin", filtros.get("ano_min", 2014))
        preco_max = filtros.get("precoMax", filtros.get("preco_max", 20000))
        km_max = filtros.get("kmMax", filtros.get("km_max", 60000))
        marca_slug = slugify(filtros.get('marca') or '')
        modelo_slug = slugify(filtros.get('modelo') or '')
        carroceria_slug = filtros.get('carroceria') or filtros.get('carroceria')

        # Derive UF (state) and optional city path for Webmotors
        uf = (cidade_uf_in.split('-')[0] or 'mg').lower()
        city_slug = slugify(cidade_nome) if cidade_nome else ''
        # Map full state names to UF if user typed state in cidade
        STATE_NAME_TO_UF = {
            'minas-gerais': 'mg', 'sao-paulo': 'sp', 'são-paulo': 'sp', 'rio-de-janeiro': 'rj',
            'espirito-santo': 'es', 'esp��rito-santo': 'es', 'parana': 'pr', 'paraná': 'pr',
        }
        if city_slug in STATE_NAME_TO_UF:
            uf = STATE_NAME_TO_UF[city_slug]
            city_slug = ''
        # Build path segment: state-only (e.g., /carros/mg) or state-city (e.g., /carros/mg-contagem)
        if city_slug and city_slug != 'belo-horizonte':
            cidade_path = f"{uf}-{city_slug}"
        else:
            cidade_path = uf

        # Build path: include marca/modelo after location if provided
        path_prefix = f"https://www.webmotors.com.br/carros/{cidade_path}"
        if marca_slug and modelo_slug:
            path_prefix += f"/{marca_slug}/{modelo_slug}"
        elif marca_slug:
            path_prefix += f"/{marca_slug}"

        base = f"{path_prefix}/de.{ano_min}?"

        # Compute estadocidade: "Minas Gerais-Contagem" or just "Minas Gerais"
        UF_TO_STATE_NAME = {
            'mg': 'Minas Gerais', 'sp': 'São Paulo', 'rj': 'Rio de Janeiro', 'es': 'Espírito Santo', 'pr': 'Paraná'
        }
        state_name = UF_TO_STATE_NAME.get(uf, uf.upper())
        if '-' in cidade_path:
            city_title = city_slug.replace('-', ' ').title()
            estadocidade_val = f"{state_name}-{city_title}"
        else:
            estadocidade_val = state_name

        params = [
            "lkid=1022",
            "tipoveiculo=carros",
            f"estadocidade={quote(estadocidade_val)}",
            f"anode={ano_min}",
            f"precoate={preco_max}",
            f"kmate={km_max}",
            "page=1"
        ]
        if carroceria_slug:
            params.append(f"carroceria={carroceria_slug}")
        if filtros.get('marca'):
            params.append(f"marca1={slugify(filtros.get('marca')).upper()}")
        if filtros.get('modelo'):
            params.append(f"modelo1={slugify(filtros.get('modelo')).upper()}")

        url = base + "&".join(params)

        # Prefer ZenRows for Webmotors if API key available
        import os, re, html
        api_key = os.getenv('ZENROWS_API_KEY') or (filtros.get('zenrows_api_key') or '').strip()
        if api_key:
            logar(f"[WEBMOTORS][ZenRows] Acessando: {url}")
            def extract_text(pattern: str, s: str) -> str:
                m = re.search(pattern, s, flags=re.S|re.I)
                if not m:
                    return ''
                txt = re.sub(r"<[^>]+>", " ", m.group(1))
                return html.unescape(re.sub(r"\s+", " ", txt).strip())

            page = 1
            total_added = 0
            while not should_stop():
                paged = re.sub(r"[?&]page=\d+", "", url)
                conj = "&" if "?" in paged else "?"
                page_url = f"{paged}{conj}page={page}"
                html_text = ''
                waits = [3000, 6000, 9000, 12000]
                for w in waits:
                    zen_url = (
                        "https://api.zenrows.com/v1/?" +
                        f"url={quote(page_url, safe='')}" +
                        f"&apikey={quote(api_key)}&js_render=true&premium_proxy=true&antibot=true&wait={w}"
                    )
                    try:
                        from urllib.request import urlopen
                        with urlopen(zen_url, timeout=90) as resp:
                            html_text = resp.read().decode('utf-8', errors='ignore')
                        try:
                            fname = f"webmotors_debug_page_{page}_wait{w}.html"
                            with open(fname, 'w', encoding='utf-8') as f:
                                f.write(html_text)
                            logar(f"[WEBMOTORS][ZenRows] HTML salvo em {fname} (tamanho {len(html_text)} bytes)")
                        except Exception:
                            pass
                        # Check if cards exist; if yes, break retries
                        probe = re.search(r'(data-testid="vehicle_card_oem_container"|_Card_)', html_text, flags=re.I)
                        if probe:
                            break
                    except Exception as e:
                        logar(f"[WEBMOTORS][ZenRows] Falha ao buscar pagina {page} com wait={w}: {e}")
                        continue

                cards = re.findall(r'(data-testid="vehicle_card_oem_container"[\s\S]*?</article>)', html_text or '', flags=re.I)
                if not cards:
                    cards = re.findall(r'(data-testid="vehicle_card_[^"]+"[\s\S]*?</article>)', html_text or '', flags=re.I)
                if not cards:
                    cards = re.findall(r'(<div[^>]+class="[^"]*_Card_[^"]*"[\s\S]*?</div>\s*</div>)', html_text or '', flags=re.I)
                if not cards:
                    cards = re.findall(r'(<article[\s\S]*?R\$[\s\S]*?</article>)', html_text or '', flags=re.I)
                if not cards:
                    # Fallback: derive cards from product links windows
                    links_abs = re.findall(r'href="(https?://www\.webmotors\.com\.br/comprar/[^"]+)"', html_text or '', flags=re.I)
                    links_rel = re.findall(r'href="(/comprar/[^"]+)"', html_text or '', flags=re.I)
                    links = []
                    seen = set()
                    for l in links_abs + links_rel:
                        full = l if l.startswith('http') else ('https://www.webmotors.com.br' + l)
                        if full not in seen:
                            seen.add(full)
                            links.append(full)
                    if links:
                        for full in links:
                            try:
                                idx = (html_text or '').find(full)
                                if idx == -1:
                                    continue
                                start = max(0, idx - 2500)
                                end = min(len(html_text or ''), idx + 2500)
                                ctx = (html_text or '')[start:end]

                                def ctx_extract(pattern: str) -> str:
                                    m = re.search(pattern, ctx, flags=re.S|re.I)
                                    if not m:
                                        return ''
                                    t = re.sub(r"<[^>]+>", " ", m.group(1))
                                    return html.unescape(re.sub(r"\s+", " ", t).strip())

                                nome = ctx_extract(r'<h2[^>]*>([\s\S]*?)</h2>')
                                preco = ctx_extract(r'data-testid="vehicle_card_oem_price"[^>]*>([\s\S]*?)</')
                                if not preco:
                                    preco = ctx_extract(r'<p[^>]*class="[^"]*_body-bold-large[^"]*"[^>]*>([\s\S]*?)</p>')
                                if not preco:
                                    preco = ctx_extract(r'<p[^>]*class="[^"]*_web-subtitle-medium[^"]*"[^>]*>([\s\S]*?)</p>')
                                if not preco:
                                    preco = ctx_extract(r'<p[^>]*class="[^"]*Price[^"]*"[^>]*>([\s\S]*?)</p>')
                                if not preco:
                                    preco = ctx_extract(r'<p[^>]*>(R\$[\s\S]*?)</p>')
                                km = ctx_extract(r'(?:\b|>)([\d\.]+\s*km)(?:\b|<)')
                                local = ''
                                alt_loc = re.search(r'<p[^>]*>([^<]*(?:MG|Minas|BH|Contagem|Betim|RJ|SP|PR)[^<]*)</p>', ctx, flags=re.I)
                                if alt_loc:
                                    local = alt_loc.group(1)
                                img_m = re.search(r'<img[^>]+src="([^"]+)"', ctx, flags=re.I)
                                imagem = img_m.group(1) if img_m else ''

                                add_dado({
                                    "Nome do Carro": nome,
                                    "Valor": preco,
                                    "KM": km,
                                    "Localização": local,
                                    "Imagem": imagem,
                                    "Link": full,
                                    "Portal": "Webmotors"
                                })
                                total_added += 1
                            except Exception:
                                continue
                    if not cards and page == 1 and total_added == 0:
                        logar("[WEBMOTORS] Nenhum carro encontrado apos esperas.")
                    if not cards:
                        break

                for block in cards:
                    try:
                        nome = extract_text(r'data-testid="vehicle_card_oem_title"[^>]*>([\s\S]*?)</', block)
                        if not nome:
                            nome = extract_text(r'<h2[^>]*>([\s\S]*?)</h2>', block)
                        preco = extract_text(r'data-testid="vehicle_card_oem_price"[^>]*>([\s\S]*?)</', block)
                        if not preco:
                            preco = extract_text(r'<p[^>]*class="[^"]*_body-bold-large[^"]*"[^>]*>([\s\S]*?)</p>', block)
                        if not preco:
                            preco = extract_text(r'<p[^>]*class="[^"]*_web-subtitle-medium[^"]*"[^>]*>([\s\S]*?)</p>', block)
                        if not preco:
                            preco = extract_text(r'<p[^>]*class="[^"]*Price[^"]*"[^>]*>([\s\S]*?)</p>', block)
                        if not preco:
                            preco = extract_text(r'<p[^>]*>(R\$[\s\S]*?)</p>', block)
                        km = extract_text(r'data-testid="vehicle_card_oem_odometer"[^>]*>([\s\S]*?)</', block)
                        local = extract_text(r'data-testid="vehicle_card_oem_year"[^>]*>[\s\S]*?</[^>]+>[\s\S]*?(?:<p[^>]*>([\s\S]*?)</p>)', block)
                        img_m = re.search(r'<img[^>]+src="([^"]+)"', block, flags=re.I)
                        imagem = img_m.group(1) if img_m else ''
                        link_m = re.search(r'<a[^>]+href="([^"]+)"', block, flags=re.I)
                        link = link_m.group(1) if link_m else ''
                        if link and link.startswith('/'):
                            link = 'https://www.webmotors.com.br' + link

                        # Fallbacks if fields missing
                        if not preco:
                            alt_price = re.search(r'(R\$\s*[\d\.,]+)', block, flags=re.I)
                            if alt_price:
                                preco = alt_price.group(1)
                        if not km:
                            alt_km = re.search(r'(?:\b|>)([\d\.]+\s*km)(?:\b|<)', block, flags=re.I)
                            if alt_km:
                                km = alt_km.group(1)
                        if not local:
                            alt_loc = re.search(r'<p[^>]*>([^<]*(?:MG|Minas|BH|Contagem|Betim|RJ|SP|PR)[^<]*)</p>', block, flags=re.I)
                            if alt_loc:
                                local = alt_loc.group(1)

                        add_dado({
                            "Nome do Carro": nome,
                            "Valor": preco,
                            "KM": km,
                            "Localiza��ão": local,
                            "Imagem": imagem,
                            "Link": link,
                            "Portal": "Webmotors"
                        })
                        total_added += 1
                    except Exception:
                        continue

                page += 1
                if page > 50:
                    break
            return

        if not api_key:
            logar("[WEBMOTORS] ZENROWS_API_KEY não definido. Usando Selenium.")

        # Fallback: Selenium scraping
        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        global current_driver
        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            current_driver = driver
        except Exception:
            pass
        logar(f"[WEBMOTORS][Selenium] Acessando: {url}")
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_container"], div[class*="_Card_"]'))
            )
        except Exception:
            pass

        pagina = 1
        while not should_stop():
            carros = driver.find_elements(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_container"], div[class*="_Card_"]')
            if not carros and pagina == 1:
                try:
                    WebDriverWait(driver, 8).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_container"], div[class*="_Card_"]'))
                    )
                    carros = driver.find_elements(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_container"], div[class*="_Card_"]')
                except Exception:
                    pass
            if not carros and pagina == 1:
                logar("[WEBMOTORS] Nenhum carro encontrado.")
            for carro in carros:
                if should_stop():
                    break
                try:
                    nome = carro.find_element(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_title"]').text
                    preco = carro.find_element(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_price"]').text
                    km = carro.find_element(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_odometer"]').text
                    local_elem = carro.find_elements(By.CSS_SELECTOR, '[data-testid="vehicle_card_oem_year"]')
                    local = local_elem[0].find_element(By.XPATH, "../../..").text.split("\n")[-1] if local_elem else ""
                    imagem_elem = carro.find_elements(By.TAG_NAME, "img")
                    imagem = imagem_elem[0].get_attribute("src") if imagem_elem else ""
                    link = carro.find_element(By.TAG_NAME, "a").get_attribute("href")

                    add_dado({
                        "Nome do Carro": nome,
                        "Valor": preco,
                        "KM": km,
                        "Localiza��ão": local,
                        "Imagem": imagem,
                        "Link": link,
                        "Portal": "Webmotors"
                    })
                except Exception:
                    continue

            try:
                next_btn = driver.find_element(By.CSS_SELECTOR, 'button[data-testid="next-page"]')
                if not next_btn.is_enabled():
                    break
                driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
                time.sleep(0.8)
                next_btn.click()
                pagina += 1
                time.sleep(2)
            except Exception:
                break

        driver.quit()

    except Exception as e:
        logar(f"[ERRO] Webmotors - Erro: {str(e)}")

def extract_mercado_details_from_html(html_text: str, forbidden_words: list) -> dict:
    details = {
        "ano": "",
        "potenciaMotor": "",
        "portas": "",
        "direcao": "",
        "cambio": "",
        "tipoDirecao": "",
        "combustivel": "",
        "quilometragem": "",
        "descricao": "",
        "palavrasProibidas": []
    }
    try:
        body = html_text or ''
        m = re.search(r"\b(19|20)\d{2}\b", body)
        if m:
            details['ano'] = m.group(0)
        m = re.search(r"([\d\.]+)\s*km", body, flags=re.I)
        if m:
            details['quilometragem'] = m.group(1).replace('.', '') + ' km'
        m = re.search(r"pot[eê]ncia[:\s\n]*([\d\.,]+\s*(hp|cv)?)", body, flags=re.I)
        if m:
            details['potenciaMotor'] = m.group(1).strip()
        m = re.search(r"\bportas?\b[:\s\n]*([\d]+)", body, flags=re.I)
        if m:
            details['portas'] = m.group(1)
        m = re.search(r"\b(direc(?:ç|c)ao|direção|tipo de direção|tipo de direcao)[:\s\n]*([A-Za-zÀ-ú0-9 ]{2,30})", body, flags=re.I)
        if m:
            details['direcao'] = m.group(2).strip(); details['tipoDirecao'] = m.group(2).strip()
        m = re.search(r"\bcombust[ií]vel[:\s\n]*([A-Za-z0-9\s/]{3,30})", body, flags=re.I)
        if m:
            details['combustivel'] = m.group(1).strip()
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if not m:
            m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
        if m:
            desc = re.sub(r"\s+", " ", m.group(1)).strip()
            details['descricao'] = desc
        else:
            m = re.search(r'<p[^>]{0,120}>([^<]{50,400})</p>', html_text, flags=re.I)
            if m:
                details['descricao'] = re.sub(r"\s+", " ", m.group(1)).strip()
        # forbidden words
        desc_norm = normalize_text(details.get('descricao',''))
        for w in forbidden_words or []:
            if normalize_text(w.strip()) and normalize_text(w.strip()) in desc_norm:
                details['palavrasProibidas'].append(w.strip())
    except Exception as e:
        logar(f"[MERCADO][HTML] erro ao extrair detalhes do html: {e}")
    return details


def scraping_mercado_livre(filtros):
    try:
        api_key = (filtros.get('zenrows_api_key') or '').strip() or os.getenv('ZENROWS_API_KEY') or ''
        if api_key:
            logar('[MERCADO_LIVRE][ZenRows] Usando ZenRows para buscar listagens e detalhes')
            localizacao_raw = filtros.get("cidadeMl") or filtros.get("cidade") or filtros.get("cidade_ml") or "belo-horizonte-minas-gerais"
            localizacao = slugify(localizacao_raw)

            marca_slug = slugify(filtros.get('marca') or '')
            modelo_slug = slugify(filtros.get('modelo') or '')

            if marca_slug and modelo_slug:
                base_url = f"https://lista.mercadolivre.com.br/carros-caminhonetes/{marca_slug}/{modelo_slug}-em-{localizacao}/"
            elif modelo_slug and not marca_slug:
                base_url = f"https://lista.mercadolivre.com.br/{modelo_slug}-em-{localizacao}/"
            elif marca_slug:
                base_url = f"https://lista.mercadolivre.com.br/carros-caminhonetes/{marca_slug}-em-{localizacao}/"
            elif localizacao:
                base_url = f"https://lista.mercadolivre.com.br/carros-caminhonetes-em-{localizacao}/"
            else:
                base_url = "https://lista.mercadolivre.com.br/veiculos/"

            # append filters to URL similarly to Selenium path
            filtros_path = []
            has_price = bool(filtros.get('precoMin') or filtros.get('preco_min'))
            has_km = bool(filtros.get('kmMin') or filtros.get('km_min'))
            has_year = bool(filtros.get('anoMin') or filtros.get('ano_min'))
            if has_price:
                price_min_val = int(filtros.get('precoMin') or filtros.get('preco_min') or 0)
                price_max_val = int(filtros.get('precoMax') or filtros.get('preco_max') or price_min_val or 0)
                filtros_path.append(f"_PriceRange_{price_min_val}-{price_max_val}")
            if has_km:
                km_min_val = int(filtros.get('kmMin') or filtros.get('km_min') or 0)
                km_max_val = int(filtros.get('kmMax') or filtros.get('km_max') or 999999)
                filtros_path.append(f"_KILOMETERS_{km_min_val}km-{km_max_val}km")
            if has_year:
                filtros_path.append(f"_YearRange_{int(filtros.get('anoMin') or filtros.get('ano_min'))}-0")
            if filtros_path and "_NoIndex_True" not in filtros_path:
                filtros_path.append("_NoIndex_True")

            url = base_url + ''.join(filtros_path)

            collected_links = []
            for page in range(1,6):
                page_url = url + ("?" if "?" not in url else "&") + f"page={page}"
                html_text = fetch_via_zenrows(page_url, api_key)
                if not html_text:
                    continue
                # find product links
                matches = re.findall(r'href=["\'](https?://[^"\']*mercadolivre\.com\.br[^"\']+)["\']', html_text, flags=re.I)
                for m in matches:
                    if m and m not in collected_links:
                        collected_links.append(m.split('?')[0])
                if len(collected_links) >= 200:
                    break

            forbidden = filtros.get('forbiddenWords') or []
            for link in collected_links:
                if should_stop():
                    break
                try:
                    d_html = fetch_via_zenrows(link, api_key)
                    if not d_html:
                        continue
                    details = extract_mercado_details_from_html(d_html, forbidden)
                    nome = ''
                    mn = re.search(r'<h1[^>]*>([^<]+)</h1>', d_html, flags=re.I)
                    if mn:
                        nome = mn.group(1).strip()
                    preco = ''
                    mp = re.search(r'(?:R\$|R\s?\$)\s*[\d\.,]+', d_html)
                    if mp:
                        preco = mp.group(0)
                    imagem = ''
                    mg = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', d_html, flags=re.I)
                    if mg:
                        imagem = mg.group(1)
                    car_data_ml = {
                        'Nome do Carro': nome or 'ML Car',
                        'Valor': preco,
                        'KM': details.get('quilometragem',''),
                        'Localização': '',
                        'Imagem': imagem,
                        'Link': link,
                        'Portal': 'Mercado Livre'
                    }
                    car_data_ml.update({
                        "Ano": details.get("ano", ''),
                        "Motor": details.get("motor", ''),
                        "Potência do Motor": details.get("potenciaMotor", ''),
                        "Portas": details.get("portas", ''),
                        "Direção": details.get("direcao", ''),
                        "Câmbio": details.get("cambio", ''),
                        "Tipo de Direção": details.get("tipoDirecao", ''),
                        "Combustível": details.get("combustivel", ''),
                        "Quilometragem": details.get("quilometragem", ''),
                        "Descrição": details.get("descricao", ''),
                        "Palavras Proibidas": details.get("palavrasProibidas", [])
                    })
                    add_dado(car_data_ml)
                except Exception as e:
                    logar(f"[MERCADO_LIVRE][ZenRows] Erro processando link {link}: {e}")
            return

        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        global current_driver
        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            current_driver = driver
        except Exception:
            pass

        # Build location slug: prefer cidadeMl, fallback to cidade
        localizacao_raw = filtros.get("cidadeMl") or filtros.get("cidade") or filtros.get("cidade_ml") or "belo-horizonte-minas-gerais"
        localizacao = slugify(localizacao_raw)

        def parse_int_filter(value, default=0):
            try:
                if value is None:
                    return default
                text = str(value).strip()
                if not text:
                    return default
                cleaned = re.sub(r"[^0-9]", "", text)
                return int(cleaned) if cleaned else default
            except Exception:
                return default

        ano_min = parse_int_filter(filtros.get("anoMin") or filtros.get("ano_min"), 0)
        preco_min = parse_int_filter(filtros.get("precoMin") or filtros.get("preco_min"), 0)
        preco_max = parse_int_filter(filtros.get("precoMax") or filtros.get("preco_max"), 0)
        km_min = parse_int_filter(filtros.get("kmMin") or filtros.get("km_min"), 0)
        km_max = parse_int_filter(filtros.get("kmMax") or filtros.get("km_max"), 0)

        base_url = "https://lista.mercadolivre.com.br/veiculos/"

        marca_slug = slugify(filtros.get('marca') or '')
        modelo_slug = slugify(filtros.get('modelo') or '')

        if marca_slug and modelo_slug:
            # /carros-caminhonetes/{marca}/{modelo}-em-{localizacao}/
            base_url += f"carros-caminhonetes/{marca_slug}/{modelo_slug}-em-{localizacao}/"
        elif modelo_slug and not marca_slug:
            # /{modelo}-em-{localizacao}/ (e.g. /civic-em-belo-horizonte/)
            base_url += f"{modelo_slug}-em-{localizacao}/"
        elif marca_slug:
            base_url += f"carros-caminhonetes/{marca_slug}-em-{localizacao}/"
        elif localizacao:
            base_url += f"carros-caminhonetes-em-{localizacao}/"
        else:
            base_url += "carros-caminhonetes/"

        filtros_path: list[str] = []
        has_price = bool(preco_min or preco_max)
        has_km = bool(km_min or km_max)
        has_year = bool(ano_min)

        if has_price:
            price_min_val = preco_min if preco_min else 0
            price_max_val = preco_max if preco_max else price_min_val or 0
            if price_max_val and price_min_val and price_min_val > price_max_val:
                price_min_val, price_max_val = price_max_val, price_min_val
            filtros_path.append(f"_PriceRange_{price_min_val}-{price_max_val}")
        if has_km:
            km_min_val = km_min if km_min else 0
            km_max_val = km_max if km_max else 999999
            if km_max_val and km_min_val and km_min_val > km_max_val:
                km_min_val, km_max_val = km_max_val, km_min_val
            filtros_path.append(f"_KILOMETERS_{km_min_val}km-{km_max_val}km")
        if has_year:
            filtros_path.append(f"_YearRange_{ano_min}-0")

        carroceria_raw = filtros.get('carroceria') or ''
        carroceria_code = None
        if carroceria_raw:
            carroceria_code = BODY_TYPE_CODES.get(normalize_text(carroceria_raw))
            if carroceria_code:
                filtros_path.append(f"_VEHICLE*BODY*TYPE_{carroceria_code}")

        # Mercado Livre requer NoIndex quando usamos filtros na URL para evitar novas paginas de categoria
        if filtros_path and "_NoIndex_True" not in filtros_path:
            filtros_path.append("_NoIndex_True")

        url = base_url + ''.join(filtros_path) + "?new_categories_landing=false"

        anchor_params = []
        # KM anchor
        if has_km:
            km_min_val = km_min if km_min else 0
            km_max_val = km_max if km_max else 999999
            km_label = f"{format_int_br(km_min_val)} a {format_int_br(km_max_val)} km".replace(' ', '+')
            anchor_params.extend([
                "applied_filter_id=KILOMETERS",
                "applied_filter_name=Quilômetros",
                "applied_filter_order=7",
                f"applied_value_id=[{km_min_val}km-{km_max_val}km]",
                f"applied_value_name={km_label}",
                "applied_value_order=0",
                "applied_value_results=0",
                "is_custom=false",
            ])
        # Carroceria anchor
        if carroceria_code and carroceria_raw:
            carroceria_label_display = carroceria_raw.strip().replace(' ', '+') if carroceria_raw else ''
            anchor_params.extend([
                "applied_filter_id=VEHICLE_BODY_TYPE",
                "applied_filter_name=Tipo+de+carroceria",
                "applied_filter_order=14",
                f"applied_value_id={carroceria_code}",
                f"applied_value_name={carroceria_label_display}",
                "applied_value_order=0",
                "applied_value_results=0",
                "is_custom=false",
            ])
        # Preco anchor (mesmo sem carroceria)
        if has_price:
            if preco_min and preco_max:
                price_id = f"{price_min_val}-{price_max_val}"
                price_name = f"{format_int_br(price_min_val)} a {format_int_br(price_max_val)}"
            elif preco_max:
                price_id = f"*-{price_max_val}"
                price_name = f"ate {format_int_br(price_max_val)}"
            else:
                price_id = f"{price_min_val}-*"
                price_name = f"a partir de {format_int_br(price_min_val)}"
            price_name_enc = price_name.replace(' ', '+')
            anchor_params.extend([
                "applied_filter_id=price",
                "applied_filter_name=Preco",
                "applied_filter_order=10",
                f"applied_value_id={price_id}",
                f"applied_value_name={price_name_enc}",
                "applied_value_order=4",
                "applied_value_results=UNKNOWN_RESULTS",
                "is_custom=true",
            ])

        anchor = ""
        if anchor_params:
            anchor = "#" + quote("&".join(anchor_params), safe='+')

        final_url = url + anchor

        logar(f"[MERCADO_LIVRE] Acessando: {final_url}")
        driver.get(final_url)
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".ui-search-result__wrapper, .ui-search-result, .ui-search-item"))
            )
        except Exception:
            pass
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
        except Exception:
            pass
        try:
            body_el = driver.find_element(By.TAG_NAME, "body")
            page_text_norm = normalize_text(body_el.text)
            if "nao ha anuncios que correspondam a sua busca" in page_text_norm:
                msg = "Não há anúncios que correspondam à sua busca"
                logar(f"[MERCADO_LIVRE] {msg}")
                return
        except Exception:
            pass

        carroceria_aplicada_via_url = bool(carroceria_code)

        # Aplicar filtros adicionais clicando nos links de filtro
        try:
            # Filtro de portas
            if filtros.get("portas"):
                portas_valor = filtros.get("portas", "").lower().strip()
                try:
                    filter_sections = driver.find_elements(By.CSS_SELECTOR, 'div.ui-search-filter-dl')
                    for section in filter_sections:
                        try:
                            title = section.find_element(By.CSS_SELECTOR, 'h3.ui-search-filter-dt-title')
                            if 'porta' in title.text.lower() or 'doors' in title.text.lower():
                                # Procurar pelos links de filtro
                                links = section.find_elements(By.CSS_SELECTOR, 'a.ui-search-link')
                                for link in links:
                                    try:
                                        filter_name = link.find_element(By.CSS_SELECTOR, '.ui-search-filter-name')
                                        if portas_valor in filter_name.text.lower():
                                            driver.execute_script("arguments[0].click();", link)
                                            time.sleep(1)
                                            logar(f"[MERCADO_LIVRE] Filtro Portas aplicado: {portas_valor}")
                                            raise StopIteration
                                    except Exception:
                                        continue
                        except StopIteration:
                            break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[MERCADO_LIVRE] Aviso: Nao foi possivel aplicar filtro de portas: {e}")

            # Filtro de transmissão
            if filtros.get("transmissao"):
                trans_valor = filtros.get("transmissao", "").lower().strip()
                try:
                    filter_sections = driver.find_elements(By.CSS_SELECTOR, 'div.ui-search-filter-dl')
                    for section in filter_sections:
                        try:
                            title = section.find_element(By.CSS_SELECTOR, 'h3.ui-search-filter-dt-title')
                            if 'transmissão' in title.text.lower() or 'transmissao' in title.text.lower():
                                links = section.find_elements(By.CSS_SELECTOR, 'a.ui-search-link')
                                for link in links:
                                    try:
                                        filter_name = link.find_element(By.CSS_SELECTOR, '.ui-search-filter-name')
                                        if trans_valor in filter_name.text.lower():
                                            driver.execute_script("arguments[0].click();", link)
                                            time.sleep(1)
                                            logar(f"[MERCADO_LIVRE] Filtro Transmissão aplicado: {trans_valor}")
                                            raise StopIteration
                                    except Exception:
                                        continue
                        except StopIteration:
                            break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[MERCADO_LIVRE] Aviso: Nao foi possivel aplicar filtro de transmissão: {e}")

            # Filtro de combustível
            if filtros.get("combustivel"):
                fuel_valor = filtros.get("combustivel", "").lower().strip()
                try:
                    filter_sections = driver.find_elements(By.CSS_SELECTOR, 'div.ui-search-filter-dl')
                    for section in filter_sections:
                        try:
                            title = section.find_element(By.CSS_SELECTOR, 'h3.ui-search-filter-dt-title')
                            if 'combustível' in title.text.lower() or 'combustivel' in title.text.lower():
                                links = section.find_elements(By.CSS_SELECTOR, 'a.ui-search-link')
                                for link in links:
                                    try:
                                        filter_name = link.find_element(By.CSS_SELECTOR, '.ui-search-filter-name')
                                        if fuel_valor in filter_name.text.lower():
                                            driver.execute_script("arguments[0].click();", link)
                                            time.sleep(1)
                                            logar(f"[MERCADO_LIVRE] Filtro Combustível aplicado: {fuel_valor}")
                                            raise StopIteration
                                    except Exception:
                                        continue
                        except StopIteration:
                            break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[MERCADO_LIVRE] Aviso: Nao foi possivel aplicar filtro de combustível: {e}")

            # Filtro de cor
            if filtros.get("cor"):
                cor_valor = filtros.get("cor", "").lower().strip()
                try:
                    filter_sections = driver.find_elements(By.CSS_SELECTOR, 'div.ui-search-filter-dl')
                    for section in filter_sections:
                        try:
                            title = section.find_element(By.CSS_SELECTOR, 'h3.ui-search-filter-dt-title')
                            if 'cor' in title.text.lower() or 'color' in title.text.lower():
                                links = section.find_elements(By.CSS_SELECTOR, 'a.ui-search-link')
                                for link in links:
                                    try:
                                        filter_name = link.find_element(By.CSS_SELECTOR, '.ui-search-filter-name')
                                        if cor_valor in filter_name.text.lower():
                                            driver.execute_script("arguments[0].click();", link)
                                            time.sleep(1)
                                            logar(f"[MERCADO_LIVRE] Filtro Cor aplicado: {cor_valor}")
                                            raise StopIteration
                                    except Exception:
                                        continue
                        except StopIteration:
                            break
                        except Exception:
                            continue
                except Exception as e:
                    logar(f"[MERCADO_LIVRE] Aviso: Nao foi possivel aplicar filtro de cor: {e}")

            time.sleep(1)
        except StopIteration:
            pass
        except Exception as e:
            logar(f"[MERCADO_LIVRE] Aviso: Erro ao aplicar filtros dinamicos: {e}")

        # If a carroceria filter was provided but not mapped, try to click it in the sidebar filters
        if carroceria_raw and not carroceria_aplicada_via_url:
            try:
                desired = normalize_text(carroceria_raw)
                sections = driver.find_elements(By.CSS_SELECTOR, 'div.ui-search-filter-dl')
                for sec in sections:
                    try:
                        h3 = sec.find_element(By.CSS_SELECTOR, 'h3.ui-search-filter-dt-title')
                        if 'carroceria' in normalize_text(h3.text) or 'tipo de carroceria' in normalize_text(h3.text):
                            links = sec.find_elements(By.CSS_SELECTOR, 'a.ui-search-link')
                            for a in links:
                                try:
                                    name_el = a.find_element(By.CSS_SELECTOR, '.ui-search-filter-name')
                                    name = normalize_text(name_el.text)
                                    if desired in name:
                                        href = a.get_attribute('href')
                                        if href:
                                            logar(f"[MERCADO_LIVRE] Aplicando filtro carroceria clicando: {name}")
                                            driver.get(href)
                                            time.sleep(2)
                                            raise StopIteration
                                except Exception:
                                    continue
                    except Exception:
                        continue
            except StopIteration:
                pass
            except Exception as e:
                logar(f"[MERCADO_LIVRE] Falha ao aplicar filtro carroceria: {e}")

        def _build_next_url_from_offset(current_url, items_on_page):
            try:
                anchor_part = ""
                if "#" in current_url:
                    current_url, anchor_part = current_url.split("#", 1)
                    anchor_part = "#" + anchor_part
                query_part = ""
                if "?" in current_url:
                    base_part, query_part = current_url.split("?", 1)
                    query_part = "?" + query_part
                else:
                    base_part = current_url
                m = re.search(r"_Desde_(\d+)", base_part)
                page_size = items_on_page if items_on_page and items_on_page > 0 else 48
                if m:
                    new_offset = int(m.group(1)) + page_size
                    base_part = re.sub(r"_Desde_\d+", f"_Desde_{new_offset}", base_part)
                else:
                    base_part = base_part + f"_Desde_{page_size + 1}"
                return base_part + query_part + anchor_part
            except Exception:
                return ""

        pagina = 1
        seen_links = set()
        prev_next_href = None
        while not should_stop():
            cards = driver.find_elements(By.CSS_SELECTOR, ".ui-search-result__wrapper, .ui-search-result, .ui-search-item")
            # gather links on this page to detect duplicates / end condition
            page_links = []
            for c in cards:
                try:
                    a_tags = c.find_elements(By.TAG_NAME, 'a')
                    if a_tags:
                        href = a_tags[0].get_attribute('href') or ''
                        if href:
                            page_links.append(href)
                except Exception:
                    continue
            if not page_links:
                logar("[MERCADO_LIVRE] Nenhum card detectado nesta página. Encerrando.")
                break
            new_links = [l for l in page_links if l not in seen_links]
            if not new_links:
                logar("[MERCADO_LIVRE] Nenhum novo anúncio encontrado nesta página. Encerrando para evitar loop.")
                break
            # Collect all cards on this page into a transient list to avoid navigating away
            cars_on_page = []
            for card in cards:
                if should_stop():
                    break
                try:
                    nome = ""
                    els = card.find_elements(By.CLASS_NAME, "poly-component__title")
                    if not els:
                        els = card.find_elements(By.CSS_SELECTOR, ".ui-search-item__title, .ui-search-item__title-label")
                    if els:
                        nome = els[0].text.strip()

                    imagem = ""
                    img_candidates = card.find_elements(By.CLASS_NAME, "poly-component__picture")
                    if not img_candidates:
                        img_candidates = card.find_elements(By.CSS_SELECTOR, ".ui-search-result__image img, picture img, img")
                    if img_candidates:
                        # prefer data-src or data-original if available, then srcset, then src
                        el = img_candidates[0]
                        try:
                            cand = el.get_attribute('data-src') or el.get_attribute('data-original') or el.get_attribute('data-lazy-src') or ''
                            if not cand:
                                # try srcset from <source> or img
                                try:
                                    parent = el.find_element(By.XPATH, './ancestor::picture')
                                    sources = parent.find_elements(By.TAG_NAME, 'source')
                                    for s in sources:
                                        srcset = s.get_attribute('srcset') or ''
                                        if srcset and 'data:image' not in srcset:
                                            # pick the last url in srcset (usually biggest)
                                            parts = [p.strip() for p in srcset.split(',') if p.strip()]
                                            if parts:
                                                urlpart = parts[-1].split(' ')[0]
                                                cand = urlpart
                                                break
                                except Exception:
                                    pass
                            if not cand:
                                # try img srcset or src
                                cand = el.get_attribute('srcset') or el.get_attribute('src') or ''
                                if cand and ',' in cand:
                                    parts = [p.strip() for p in cand.split(',') if p.strip()]
                                    # choose the first non-data url
                                    selected = ''
                                    for part in parts:
                                        url = part.split(' ')[0]
                                        if url and not url.startswith('data:'):
                                            selected = url
                                            break
                                    if selected:
                                        cand = selected
                            # final check to avoid data:image
                            if cand and cand.startswith('data:'):
                                # try to find any <img> inside card with meaningful src
                                try:
                                    imgs_alt = card.find_elements(By.CSS_SELECTOR, 'img')
                                    for ia in imgs_alt:
                                        s = ia.get_attribute('data-src') or ia.get_attribute('srcset') or ia.get_attribute('src') or ''
                                        if s and not s.startswith('data:'):
                                            cand = s
                                            break
                                except Exception:
                                    pass
                            imagem = cand or ''
                        except Exception:
                            try:
                                imagem = el.get_attribute('src') or el.get_attribute('data-src') or ''
                            except Exception:
                                imagem = ''

                    valor = ""
                    p = card.find_elements(By.CLASS_NAME, "andes-money-amount__fraction")
                    if not p:
                        p = card.find_elements(By.CLASS_NAME, "price-tag-fraction")
                    if p:
                        valor = p[0].text

                    local = ""
                    l = card.find_elements(By.CLASS_NAME, "poly-component__location")
                    if l:
                        local = l[0].text.strip()

                    km = "N/A"
                    try:
                        km_el = card.find_element(By.XPATH, ".//li[contains(text(),'Km')]")
                        km = km_el.text
                    except Exception:
                        pass

                    link = ""
                    a = card.find_elements(By.TAG_NAME, "a")
                    if a:
                        link = a[0].get_attribute("href") or ""

                    if nome:
                        car_data_ml = {
                            "Nome do Carro": nome,
                            "Valor": f"R$ {valor}" if valor else "Preço não informado",
                            "KM": km,
                            "Localização": local or localizacao.replace('-', ' ').title(),
                            "Imagem": imagem,
                            "Link": link,
                            "Portal": "Mercado Livre"
                        }
                        cars_on_page.append((car_data_ml, link))
                        if link:
                            seen_links.add(link)
                except Exception:
                    continue

            # Process details for the collected cars in this page (open details in new tab to avoid losing listing)
            capture = filtros.get('capture_details', True)
            if capture:
                for (car_data_ml, link) in cars_on_page:
                    try:
                        if not link:
                            add_dado(car_data_ml)
                            continue
                        existing_handles = set(driver.window_handles)
                        try:
                            driver.execute_script("window.open(arguments[0], '_blank');", link)
                            WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(existing_handles))
                            new_handles = [h for h in driver.window_handles if h not in existing_handles]
                            if new_handles:
                                driver.switch_to.window(new_handles[0])
                            else:
                                driver.get(link)
                        except Exception:
                            driver.get(link)

                        try:
                            WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.andes-table, .ui-pdp-description__content")))
                        except Exception:
                            time.sleep(1.0)

                        # Attempt to expand 'Conferir todas as características' collapsible if present
                        try:
                            expand_selectors = ["button[data-testid='action-collapsable-target']", ".ui-pdp-collapsable__action", "button.ui-pdp-collapsable__action"]
                            expand_btn = None
                            # wait briefly for element to appear
                            start = time.time()
                            while time.time() - start < 3:
                                for sel in expand_selectors:
                                    try:
                                        el = driver.find_element(By.CSS_SELECTOR, sel)
                                        if el and el.is_displayed():
                                            expand_btn = el
                                            break
                                    except Exception:
                                        continue
                                if expand_btn:
                                    break
                                time.sleep(0.2)

                            if expand_btn:
                                clicked = False
                                for attempt in range(2):
                                    try:
                                        driver.execute_script("arguments[0].scrollIntoView(true);", expand_btn)
                                        time.sleep(0.15)
                                        try:
                                            expand_btn.click()
                                        except Exception:
                                            driver.execute_script("arguments[0].click();", expand_btn)
                                        clicked = True
                                    except Exception:
                                        clicked = False
                                    # wait a bit for content to expand
                                    time.sleep(0.8 + attempt*0.4)
                                    # check if highlighted specs or additional tables are now present
                                    try:
                                        has_tables = len(driver.find_elements(By.CSS_SELECTOR, "table.andes-table tbody tr")) > 0
                                        has_highlight = len(driver.find_elements(By.CSS_SELECTOR, "#highlighted_specs_attrs, .ui-vpp-highlighted-specs, .ui-vpp-striped-specs")) > 0
                                        if has_tables or has_highlight:
                                            break
                                    except Exception:
                                        pass
                                # as a fallback, try clicking via JS directly on selectors again
                                if not clicked:
                                    try:
                                        el = driver.find_element(By.CSS_SELECTOR, "button[data-testid='action-collapsable-target']")
                                        driver.execute_script("arguments[0].click();", el)
                                        time.sleep(0.8)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        try:
                            rows = driver.find_elements(By.CSS_SELECTOR, "table.andes-table tbody tr")
                            for row in rows:
                                try:
                                    # Prefer explicit <th> label and <td> value; fall back to any cell texts
                                    lbl = ''
                                    val = ''
                                    try:
                                        th = row.find_element(By.TAG_NAME, 'th')
                                        lbl = (th.text or '').lower().strip()
                                    except Exception:
                                        try:
                                            th_div = row.find_element(By.CSS_SELECTOR, '.andes-table__header__container')
                                            lbl = (th_div.text or '').lower().strip()
                                        except Exception:
                                            # fallback: use first cell
                                            cells_fb = row.find_elements(By.TAG_NAME, 'td')
                                            if cells_fb:
                                                lbl = (cells_fb[0].text or '').lower().strip()
                                    # value: prefer the .andes-table__column--value span inside the td
                                    try:
                                        td_val = row.find_element(By.CSS_SELECTOR, 'td .andes-table__column--value')
                                        val = (td_val.text or '').strip()
                                    except Exception:
                                        try:
                                            tds = row.find_elements(By.TAG_NAME, 'td')
                                            if len(tds) >= 1:
                                                # if two cells and first is header, take second
                                                if len(tds) >= 2:
                                                    val = (tds[1].text or '').strip()
                                                else:
                                                    val = (tds[0].text or '').strip()
                                        except Exception:
                                            val = ''

                                    # quilometragem
                                    if ("km" in lbl) or ('quilô' in lbl) or ('quilom' in lbl) or ('quilometragem' in lbl):
                                        car_data_ml["Quilometragem"] = val
                                        car_data_ml["quilometragem"] = val
                                    # câmbio
                                    elif ("cambio" in lbl) or ("câmbio" in lbl) or ("transmiss" in lbl):
                                        car_data_ml["Cambio"] = val
                                        car_data_ml["cambio"] = val
                                        car_data_ml["Câmbio"] = val
                                    # motor (ex: "Motor: 1.0") -> treat as engine displacement and map to potenciaMotor
                                    elif ('motor' in lbl) and not ('potência' in lbl or 'potencia' in lbl):
                                        car_data_ml["Motor"] = val
                                        car_data_ml["motor"] = val
                                        # keep a consistent key used elsewhere for filtering/ranking
                                        car_data_ml["potenciaMotor"] = val
                                        car_data_ml["Potência do Motor"] = val
                                    # potência / horsepower (ex: "Potência: 68,8 hp") -> map to potencia/hp fields only
                                    elif ("potência" in lbl) or ("potencia" in lbl) or ('hp' in val.lower()) or ('cv' in val.lower()) or ("engine" in lbl):
                                        car_data_ml["Potencia"] = val
                                        car_data_ml["potencia"] = val
                                        # keep the explicit horsepower field but do NOT overwrite potenciaMotor (displacement)
                                        car_data_ml["Potência (hp)"] = val
                                    # portas: prefer explicit numeric extraction. Use word boundaries to avoid false matches like 'porta copos'
                                    elif re.search(r"\bportas?\b", lbl) and not re.search(r"copos|porta copos", lbl):
                                        portas_raw = val
                                        # extract first integer from value (e.g., '4', '4 portas'). If none, try to normalize common answers like 'sim'/'não' -> leave as-is
                                        m = re.search(r"(\d+)", portas_raw)
                                        portas_val = m.group(1) if m else portas_raw
                                        car_data_ml["Portas"] = portas_val
                                        car_data_ml["portas"] = portas_val
                                    # ano / year
                                    elif ("ano" in lbl) or ("ano de" in lbl) or ('fabricado' in lbl) or ('year' in lbl):
                                        car_data_ml["Ano"] = val
                                        car_data_ml["ano"] = val
                                except Exception:
                                    continue
                        except Exception:
                            pass

                        # Some Mercado Livre pages put key specs in highlighted specs area (not in the andes-table). Parse those too.
                        try:
                            spec_rows = driver.find_elements(By.CSS_SELECTOR, ".ui-vpp-highlighted-specs__key-value__labels p, .ui-vpp-highlighted-specs__key-value__labels__key-value, .ui-pdp-container__row.ui-vpp-highlighted-specs__attribute-columns .ui-vpp-highlighted-specs__key-value__labels")
                            for s in spec_rows:
                                try:
                                    txt = s.text or ''
                                    key = ''
                                    value = ''
                                    if ':' in txt:
                                        parts = [p.strip() for p in txt.split(':', 1)]
                                        if len(parts) == 2:
                                            key = parts[0].lower()
                                            value = parts[1]
                                        else:
                                            continue
                                    else:
                                        # try to find child spans for key/value
                                        spans = s.find_elements(By.TAG_NAME, 'span')
                                        if len(spans) >= 2:
                                            key = spans[0].text.lower()
                                            value = spans[1].text
                                        else:
                                            continue
                                    # If the label says 'motor', prefer this as the engine displacement and map to potenciaMotor
                                    if 'motor' in key:
                                        car_data_ml["Motor"] = value
                                        car_data_ml["motor"] = value
                                        car_data_ml["potenciaMotor"] = value
                                        car_data_ml["Potência do Motor"] = value
                                    # Only map horsepower when explicitly labeled as potência or unit contains hp/cv
                                    elif ('potência' in key) or ('potencia' in key) or ('hp' in (value or '').lower()) or ('cv' in (value or '').lower()):
                                        car_data_ml["Potencia"] = value
                                        car_data_ml["potencia"] = value
                                        car_data_ml["Potência (hp)"] = value
                                    elif re.search(r"\bportas?\b", key) and not re.search(r"copos|porta copos", key):
                                        car_data_ml["Portas"] = value
                                        car_data_ml["portas"] = value
                                    elif ('ano' in key) or ('fabricado' in key):
                                        car_data_ml["Ano"] = value
                                        car_data_ml["ano"] = value
                                except Exception:
                                    continue
                        except Exception:
                            pass

                        try:
                            desc_el = driver.find_element(By.CSS_SELECTOR, ".ui-pdp-description__content")
                            desc_text = desc_el.text.strip()
                            car_data_ml["Descricao"] = desc_text
                            car_data_ml["descricao"] = desc_text
                            car_data_ml["Descrição"] = desc_text
                        except Exception:
                            pass

                        # close tab and return to listing
                        try:
                            driver.close()
                        except Exception:
                            try:
                                driver.execute_script("window.close();")
                            except Exception:
                                pass
                        time.sleep(0.3)
                        try:
                            if driver.window_handles:
                                driver.switch_to.window(driver.window_handles[0])
                        except Exception:
                            pass

                        add_dado(car_data_ml)
                    except Exception as e:
                        logar(f"[MERCADO_LIVRE] Erro ao processar detalhe: {e}")
                        add_dado(car_data_ml)
                        continue
            else:
                for (car_data_ml, _) in cars_on_page:
                    add_dado(car_data_ml)

            pagina += 1
            next_href = ""
            try:
                selectors = [
                    "li.andes-pagination__button--next a",
                    "a[rel='next']",
                    "li.andes-pagination__arrow--next a",
                    "a[aria-label*='Seguinte']",
                    "a[title*='Seguinte']",
                ]
                for sel in selectors:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, sel)
                        href = el.get_attribute("href") or ""
                        if href:
                            next_href = href
                            break
                    except Exception:
                        continue
            except Exception:
                next_href = ""
            if not next_href:
                next_href = _build_next_url_from_offset(driver.current_url, len(cards))
            # If next_href is same as previous attempted next, probably no progress
            if not next_href or next_href == driver.current_url or next_href == prev_next_href:
                logar("[MERCADO_LIVRE] Sem próxima página válida ou rotina detectou página repetida. Encerrando.")
                break
            try:
                logar(f"[MERCADO_LIVRE] Navegando para a proxima pagina: {next_href}")
                prev_next_href = next_href
                driver.get(next_href)
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".ui-search-result__wrapper, .ui-search-result, .ui-search-item"))
                    )
                except Exception:
                    pass
                time.sleep(1.0)
            except Exception:
                break

        driver.quit()

    except Exception as e:
        logar(f"[ERRO] Mercado Livre - Erro: {str(e)}")

def extract_details_seminovos(driver, link, forbidden_words):
    """Extract detailed information from Seminovos car detail page"""
    details = {
        "quilometragem": "",
        "cambio": "",
        "ano": "",
        "portas": "",
        "combustivel": "",
        "cor": "",
        "descricao": "",
        "palavrasProibidas": []
    }

    try:
        logar(f"[SEMINOVOS] Abrindo página de detalhes: {link}")
        log_seminovos(f"Handles antes de abrir: {driver.window_handles}")
        original_window = driver.current_window_handle
        existing_handles = set(driver.window_handles)
        used_new_tab = False
        try:
            driver.execute_script("window.open(arguments[0], '_blank');", link)
            WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > len(existing_handles))
            new_handles = [h for h in driver.window_handles if h not in existing_handles]
            log_seminovos(f"Handles depois de abrir: {driver.window_handles}, new_handles: {new_handles}")
            if new_handles:
                # Switch to the newly opened tab and mark that we opened a new tab
                new_handle = new_handles[0]
                try:
                    driver.switch_to.window(new_handle)
                    used_new_tab = True
                    log_seminovos(f"Switch para nova janela: {new_handle}")
                except Exception as e:
                    log_seminovos(f"Falha ao switch para new_handle {new_handle}: {e}")
                    # fallback: navigate in same tab
                    used_new_tab = False
                    driver.get(link)
                    log_seminovos("Fallback: navegou no mesmo tab para o detalhe")
            else:
                # Fallback to navigating in the same tab
                used_new_tab = False
                driver.get(link)
                log_seminovos("Fallback: navegou no mesmo tab para o detalhe")
        except Exception as e:
            log_seminovos(f"Erro abrindo nova aba: {e}")
            # Ensure flag is explicit when falling back
            used_new_tab = False
            driver.get(link)
        time.sleep(1.5)

        try:
            detalhes_container = driver.find_element(By.CSS_SELECTOR, ".part-items-detalhes-icones")
            itens = detalhes_container.find_elements(By.CSS_SELECTOR, ".item")
            log_seminovos(f"Encontrados {len(itens)} itens de detalhe na página")

            for item in itens:
                try:
                    titulo_el = item.find_element(By.CSS_SELECTOR, ".campo")
                    titulo = titulo_el.text.lower().strip()
                    valor_el = item.find_element(By.CSS_SELECTOR, ".valor")
                    valor = valor_el.text.strip()
                    log_seminovos(f"Detalhe: {titulo} -> {valor}")

                    if "quilometragem" in titulo:
                        details["quilometragem"] = valor
                    elif "cambio" in titulo or "transmiss" in titulo:
                        details["cambio"] = valor
                    elif "ano" in titulo:
                        details["ano"] = valor
                    elif "porta" in titulo:
                        details["portas"] = valor
                    elif "combustivel" in titulo or "combust" in titulo:
                        details["combustivel"] = valor
                    elif "cor" in titulo:
                        details["cor"] = valor
                except Exception as e:
                    log_seminovos(f"Erro lendo item de detalhe: {e}")
                    continue
        except Exception as e:
            log_seminovos(f"Aviso ao extrair detalhes tecnicos: {e}")

        try:
            desc_container = driver.find_element(By.CSS_SELECTOR, ".part-sobre-veiculo-acessorios p")
            desc_text = desc_container.text.strip()
            details["descricao"] = desc_text
            log_seminovos(f"Descricao extraida ({len(desc_text)} chars)")

            desc_normalized = normalize_text(desc_text)
            for word in forbidden_words:
                word_normalized = normalize_text(word.strip())
                if word_normalized and word_normalized in desc_normalized:
                    details["palavrasProibidas"].append(word.strip())
        except Exception as e:
            log_seminovos(f"Aviso ao extrair descricao: {e}")

    except Exception as e:
        logar(f"[SEMINOVOS] Erro ao extrair detalhes: {e}")
    finally:
        try:
            # If we opened a new tab, close it and return to the original window
            if 'used_new_tab' in locals() and used_new_tab:
                try:
                    # If we tracked the new_handle, switch to it explicitly before closing
                    if 'new_handle' in locals() and new_handle in driver.window_handles:
                        try:
                            driver.switch_to.window(new_handle)
                        except Exception:
                            pass
                    # Close the detail tab
                    try:
                        driver.close()
                    except Exception:
                        try:
                            driver.execute_script("window.close();")
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(0.4)
                try:
                    if 'original_window' in locals() and original_window in driver.window_handles:
                        driver.switch_to.window(original_window)
                    elif driver.window_handles:
                        driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass
            else:
                # We navigated in the same tab; try to go back to the listing page
                try:
                    driver.back()
                except Exception:
                    try:
                        # As a last resort, if original_window exists, switch to it
                        if 'original_window' in locals() and original_window in driver.window_handles:
                            driver.switch_to.window(original_window)
                    except Exception:
                        pass
        except Exception as e:
            logar(f"[SEMINOVOS] Erro ao voltar da página de detalhes: {e}")

    return details

def scraping_seminovos(filtros):
    try:
        # Prefer ZenRows for Seminovos if API key available
        api_key = (filtros.get('zenrows_api_key') or '').strip() or os.getenv('ZENROWS_API_KEY') or ''
        if api_key:
            logar('[SEMINOVOS][ZenRows] Usando ZenRows para buscar listagens e detalhes - modo rápido')
            marca_slug = slugify(filtros.get('marca') or '')
            modelo_slug = slugify(filtros.get('modelo') or '')
            carroceria_slug = slugify(filtros.get('carroceria') or '')
            base_candidates = [
                'https://seminovos.com.br',
                'https://seminovos.unidas.com.br',
                'https://seminovos.localiza.com'
            ]
            collected_links = []
            forbidden = filtros.get('forbiddenWords') or []
            # collect links from candidate domains quickly
            for base in base_candidates:
                if should_stop():
                    logar('[SEMINOVOS][ZenRows] Abortado pelo usuário durante coleta de links')
                    return
                try:
                    list_url = base
                    if marca_slug and modelo_slug:
                        list_url = f"{base}/busca/{marca_slug}/{modelo_slug}"
                    elif marca_slug:
                        list_url = f"{base}/busca/{marca_slug}"
                    elif modelo_slug:
                        list_url = f"{base}/busca/{modelo_slug}"
                    # fetch fast with minimal waits
                    html_text = fetch_via_zenrows(list_url, api_key, waits=(400, 900))
                    if not html_text:
                        continue
                    matches = re.findall(r'href=["\'](https?://[^"\']*(?:seminovos|unidas|localiza)[^"\']+)["\']', html_text, flags=re.I)
                    for m in matches:
                        link = m.split('?')[0]
                        if link not in collected_links:
                            collected_links.append(link)
                    if len(collected_links) >= 300:
                        break
                except Exception as e:
                    log_seminovos(f"[ZenRows] erro ao coletar links em {base}: {e}")
                    continue

            # Process links via ZenRows with short waits for speed
            for link in collected_links:
                if should_stop():
                    logar('[SEMINOVOS][ZenRows] Abortado pelo usuário durante processamento de links')
                    return
                try:
                    d_html = fetch_via_zenrows(link, api_key, waits=(300, 600))
                    if not d_html:
                        continue
                    # Basic extraction to populate common fields fast
                    nome = ''
                    m = re.search(r'<h1[^>]*>([^<]+)</h1>', d_html, flags=re.I)
                    if m:
                        nome = m.group(1).strip()
                    preco = ''
                    mp = re.search(r'(?:R\$|R\s?\$)\s*[\d\.,]+', d_html)
                    if mp:
                        preco = mp.group(0)
                    imagem = ''
                    mg = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', d_html, flags=re.I)
                    if mg:
                        imagem = mg.group(1)
                    quilometragem = ''
                    mq = re.search(r'([\d\.]+)\s*km', d_html, flags=re.I)
                    if mq:
                        quilometragem = mq.group(1).replace('.', '') + ' km'

                    car_data = {
                        'Nome do Carro': nome or 'Seminovos Car',
                        'Valor': preco,
                        'KM': quilometragem,
                        'Localização': '',
                        'Imagem': imagem or '',
                        'Link': link,
                        'Portal': 'Seminovos'
                    }

                    # Attempt to extract additional details using existing detail extractor if available
                    try:
                        details = extract_details_seminovos_from_html(d_html, filtros.get('forbiddenWords', []))
                    except Exception:
                        details = None

                    if details and isinstance(details, dict):
                        car_data.update({
                            'Ano': details.get('ano',''),
                            'Motor': details.get('motor',''),
                            'Potência do Motor': details.get('potenciaMotor',''),
                            'Portas': details.get('portas',''),
                            'Direção': details.get('direcao',''),
                            'Câmbio': details.get('cambio',''),
                            'Tipo de Direção': details.get('tipoDirecao',''),
                            'Combustível': details.get('combustivel',''),
                            'Quilometragem': details.get('quilometragem',''),
                            'Descrição': details.get('descricao',''),
                            'Palavras Proibidas': details.get('palavrasProibidas', [])
                        })

                    add_dado(car_data)
                except Exception as e:
                    log_seminovos(f"[SEMINOVOS][ZenRows] Erro processando {link}: {e}")
            return

        # Selenium fallback: create driver and proceed (ensure we close driver immediately when stop requested)
        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            global current_driver
            current_driver = driver
        except Exception:
            pass

        # Build seminovos URL using provided filters
        marca_slug = slugify(filtros.get('marca') or '')
        modelo_slug = slugify(filtros.get('modelo') or '')

        def _num(key_variants):
            for k in key_variants:
                v = filtros.get(k)
                if v is not None and v != "":
                    try:
                        return int(v)
                    except Exception:
                        try:
                            return int(str(v).replace('.', '').replace(',', ''))
                        except Exception:
                            return None
            return None

        ano_min = _num(['anoMin', 'ano_min', 'anoMin'])
        ano_max = _num(['anoMax', 'ano_max', 'anoMax'])
        preco_min = _num(['precoMin', 'preco_min', 'precoMin'])
        preco_max = _num(['precoMax', 'preco_max', 'precoMax'])
        km_min = _num(['kmMin', 'km_min', 'kmMin'])
        km_max = _num(['kmMax', 'km_max', 'kmMax'])

        # cidade can be an id (numeric) or a slug/name
        cidade_raw = filtros.get('cidade') or filtros.get('cidade_id') or filtros.get('cidadeId') or ''
        cidade_segment = ''
        if cidade_raw:
            # prefer explicit numeric city id when provided
            if str(cidade_raw).isdigit():
                cidade_segment = f"cidade[]-{int(cidade_raw)}"
            else:
                # Some sites expect numeric city ids (e.g., Belo Horizonte => 2700).
                # Provide a small, maintainable mapping for common cities; fallback to slug.
                city_name_to_id = {
                    slugify('belo horizonte'): 2700,
                    'bh': 2700,
                    'contagem': 2922,
                    'ibirite': 3148,
                    'betim': 2707,
                    'nova-lima': 3422,
                    'sabará': 3666,
                    'sabara': 3666,
                    'santa-luzia': 3691,
                    'joao-monlevade': 3246,
                    'joão-monlevade': 3246,
                    'joao monlevade': 3246
                }
                slug = slugify(cidade_raw)
                if slug in city_name_to_id:
                    cidade_segment = f"cidade[]-{city_name_to_id[slug]}"
                else:
                    cidade_segment = f"cidade-{slug}"

        path_parts = []
        # Always use singular 'carro' as base path
        path_parts.append('carro')
        if marca_slug:
            path_parts.append(marca_slug)
        if modelo_slug:
            path_parts.append(modelo_slug)

        if cidade_segment:
            path_parts.append(cidade_segment)

        if ano_min is not None or ano_max is not None:
            amin = '' if ano_min is None else str(ano_min)
            amax = '' if ano_max is None else str(ano_max)
            path_parts.append(f"ano-{amin}-{amax}")

        if preco_min is not None or preco_max is not None:
            pmin = '' if preco_min is None else str(preco_min)
            pmax = '' if preco_max is None else str(preco_max)
            path_parts.append(f"preco-{pmin}-{pmax}")

        if km_min is not None or km_max is not None:
            kmin = '' if km_min is None else str(km_min)
            kmax = '' if km_max is None else str(km_max)
            path_parts.append(f"km-{kmin}-{kmax}")

        base_url = "https://seminovos.com.br"
        url_path = "/".join([p for p in path_parts if p])
        url = f"{base_url}/{url_path}" if url_path else f"{base_url}/carros"

        logar(f"[SEMINOVOS] Acessando: {url}")
        driver.get(url)
        time.sleep(2)

        # Aplicar filtros adicionais via dropdowns/selects
        try:
            # Filtro de portas
            if filtros.get("portas"):
                portas_valor = filtros.get("portas", "")
                try:
                    selects = driver.find_elements(By.CSS_SELECTOR, 'select[name="portas"], select[name*="door"], select[name*="porta"]')
                    for select_el in selects:
                        options = select_el.find_elements(By.TAG_NAME, "option")
                        for option in options:
                            if portas_valor in option.text:
                                option.click()
                                time.sleep(0.5)
                                logar(f"[SEMINOVOS] Filtro Portas aplicado: {portas_valor}")
                                break
                except Exception as e:
                    logar(f"[SEMINOVOS] Aviso: Nao foi possivel aplicar filtro de portas: {e}")

            # Filtro de combustível
            if filtros.get("combustivel"):
                combustivel_valor = filtros.get("combustivel", "")
                try:
                    selects = driver.find_elements(By.CSS_SELECTOR, 'select[name*="fuel"], select[name*="combust"], select[name*="carburante"]')
                    for select_el in selects:
                        options = select_el.find_elements(By.TAG_NAME, "option")
                        for option in options:
                            if combustivel_valor.lower() in option.text.lower():
                                option.click()
                                time.sleep(0.5)
                                logar(f"[SEMINOVOS] Filtro Combust��vel aplicado: {combustivel_valor}")
                                break
                except Exception as e:
                    logar(f"[SEMINOVOS] Aviso: Nao foi possivel aplicar filtro de combustível: {e}")

            # Filtro de transmissão
            if filtros.get("transmissao"):
                transmissao_valor = filtros.get("transmissao", "")
                try:
                    selects = driver.find_elements(By.CSS_SELECTOR, 'select[name*="trans"], select[name*="cambio"]')
                    for select_el in selects:
                        options = select_el.find_elements(By.TAG_NAME, "option")
                        for option in options:
                            if transmissao_valor.lower() in option.text.lower():
                                option.click()
                                time.sleep(0.5)
                                logar(f"[SEMINOVOS] Filtro Transmissão aplicado: {transmissao_valor}")
                                break
                except Exception as e:
                    logar(f"[SEMINOVOS] Aviso: Nao foi possivel aplicar filtro de transmissão: {e}")

            # Filtro de cor
            if filtros.get("cor"):
                cor_valor = filtros.get("cor", "")
                try:
                    selects = driver.find_elements(By.CSS_SELECTOR, 'select[name*="cor"], select[name*="color"]')
                    for select_el in selects:
                        options = select_el.find_elements(By.TAG_NAME, "option")
                        for option in options:
                            if cor_valor.lower() in option.text.lower():
                                option.click()
                                time.sleep(0.5)
                                logar(f"[SEMINOVOS] Filtro Cor aplicado: {cor_valor}")
                                break
                except Exception as e:
                    logar(f"[SEMINOVOS] Aviso: Nao foi possivel aplicar filtro de cor: {e}")

            # Filtro de tipo de veículo
            if filtros.get("tipo_veiculo"):
                tipo_valor = filtros.get("tipo_veiculo", "")
                try:
                    selects = driver.find_elements(By.CSS_SELECTOR, 'select[name*="tipo"], select[name*="vehicle"], select[name*="tipo_veiculo"]')
                    for select_el in selects:
                        options = select_el.find_elements(By.TAG_NAME, "option")
                        for option in options:
                            if tipo_valor.lower() in option.text.lower():
                                option.click()
                                time.sleep(0.5)
                                logar(f"[SEMINOVOS] Filtro Tipo de Veículo aplicado: {tipo_valor}")
                                break
                except Exception as e:
                    logar(f"[SEMINOVOS] Aviso: Nao foi possivel aplicar filtro de tipo: {e}")

            time.sleep(1)
        except Exception as e:
            logar(f"[SEMINOVOS] Aviso: Erro ao aplicar filtros dinamicos: {e}")

        # Carregar todos os anuncios: rolar ate o fim e clicar "CARREGAR MAIS ANUNCIOS" quando presente
        def _find_load_more_button():
            """Prefer specific .btn-mais-anuncios button. Return element or None."""
            try:
                els = driver.find_elements(By.CSS_SELECTOR, '.btn-mais-anuncios')
                for el in els:
                    try:
                        # prefer visible
                        if el.is_displayed():
                            return el
                    except Exception:
                        continue
            except Exception:
                pass
            # fallback: search by text similar to previous behavior
            try:
                candidates = driver.find_elements(By.CSS_SELECTOR, 'button, a, div')
                for el in candidates:
                    try:
                        txt = normalize_text(el.text or '')
                        if not txt:
                            continue
                        if ('carregar mais' in txt) and ('anuncio' in txt or 'anuncios' in txt):
                            return el
                    except Exception:
                        continue
            except Exception:
                return None
            return None

        def _is_disabled_load_more(el):
            try:
                cls = (el.get_attribute('class') or '')
                if 'disabled' in cls.split():
                    return True
                # check disabled attribute
                if el.get_attribute('disabled'):
                    return True
                style = (el.get_attribute('style') or '')
                if 'opacity: 0.2' in style.replace(' ', ''):
                    return True
            except Exception:
                return False
            return False

        max_cycles = 120
        last_total = 0
        while not should_stop() and max_cycles > 0:
            max_cycles -= 1

            # scroll to bottom first (user requested behavior)
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
            time.sleep(0.6)

            # try to locate load more button
            btn = _find_load_more_button()
            if btn:
                try:
                    # If button already disabled, stop
                    if _is_disabled_load_more(btn):
                        logar("[SEMINOVOS] Botao 'Carregar mais anuncios' encontrado DESABILITADO. Parando carregamento.")
                        break

                    # scroll to it and click
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    except Exception:
                        pass
                    time.sleep(0.3)
                    try:
                        btn.click()
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", btn)
                        except Exception:
                            pass

                    # after click, keep scrolling a bit to allow loading
                    for _ in range(4):
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(0.6)

                    # if after click the button becomes disabled, we are done
                    try:
                        # re-find the button reference (DOM may have been updated)
                        btn2 = _find_load_more_button()
                        if btn2 and _is_disabled_load_more(btn2):
                            logar("[SEMINOVOS] Botao 'Carregar mais anuncios' tornou-se DESABILITADO apos clique. Parando.")
                            break
                    except Exception:
                        pass

                except Exception as e:
                    logar(f"[SEMINOVOS] Erro ao clicar em Carregar mais anuncios: {e}")

                # small pause then re-evaluate counts to continue or finish
                try:
                    total_now = len(driver.find_elements(By.CLASS_NAME, "anuncio-container"))
                except Exception:
                    total_now = last_total

                # if no growth after this iteration, assume no more results and stop
                if total_now == last_total:
                    # final attempts: try scrolling a couple more times then break
                    for _ in range(3):
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(0.6)
                    try:
                        total_now = len(driver.find_elements(By.CLASS_NAME, "anuncio-container"))
                    except Exception:
                        total_now = last_total
                    if total_now == last_total:
                        logar("[SEMINOVOS] Nenhum novo anuncio apos tentativa de carregar. Encerrando.")
                        break
                last_total = total_now
                continue

            # if no button found, do a few scrolls and check if new items appeared; if not, break
            for _ in range(3):
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                except Exception:
                    pass
                time.sleep(0.6)
            try:
                total_now = len(driver.find_elements(By.CLASS_NAME, "anuncio-container"))
            except Exception:
                total_now = last_total
            if total_now == last_total:
                logar("[SEMINOVOS] Sem botao 'Carregar mais anuncios' e sem crescimento apos rolagem. Encerrando.")
                break
            last_total = total_now

        anuncios = driver.find_elements(By.CLASS_NAME, "anuncio-container")
        if not anuncios:
            logar("[SEMINOVOS] Nenhum anuncio encontrado.")

        # FASE 1: Coletar todos os resultados e links sem abrir páginas de detalhe
        logar(f"[SEMINOVOS] FASE 1: Coletando {len(anuncios)} anúncios...")
        log_seminovos(f"Total anuncios coletados no DOM: {len(anuncios)}")
        cars_to_process = []

        for idx, anuncio in enumerate(anuncios):
            if should_stop():
                break
            try:
                log_seminovos(f"Iniciando coleta do anuncio #{idx+1}")
                # Try to read structured JSON-LD safely without clicking any elements
                script = None
                try:
                    scripts = anuncio.find_elements(By.CSS_SELECTOR, 'script[type="application/ld+json"]')
                    if scripts:
                        script = scripts[0].get_attribute('innerHTML')
                        log_seminovos(f"Anuncio #{idx+1}: encontrou JSON-LD script")
                    else:
                        scrs = anuncio.find_elements(By.TAG_NAME, 'script')
                        if scrs:
                            script = scrs[0].get_attribute('innerHTML')
                            log_seminovos(f"Anuncio #{idx+1}: encontrou script tag (fallback)")
                except Exception as e:
                    log_seminovos(f"Anuncio #{idx+1}: erro ao obter scripts: {e}")
                    script = None

                json_data = {}
                if script:
                    try:
                        json_data = json.loads(script)
                        log_seminovos(f"Anuncio #{idx+1}: JSON parse ok")
                    except Exception:
                        try:
                            m = re.search(r"\{.*\}", script, flags=re.S)
                            if m:
                                json_data = json.loads(m.group(0))
                                log_seminovos(f"Anuncio #{idx+1}: JSON parse de substring OK")
                        except Exception as e:
                            log_seminovos(f"Anuncio #{idx+1}: falha ao parsear JSON: {e}")
                            json_data = {}

                nome = json_data.get("name", "") if isinstance(json_data, dict) else ""
                imagem = json_data.get("image", "") if isinstance(json_data, dict) else ""
                preco = json_data.get("offers", {}).get("price", "") if isinstance(json_data, dict) else ""
                km = ""
                if isinstance(json_data, dict):
                    km = json_data.get("mileageFromOdometer", {}).get("value", "")
                # Prefer anchor hrefs from the card (they point to the detail page). Only fall back to JSON-LD url when necessary.
                link = ''

                # Fallbacks - only read attributes/text, do not click
                try:
                    if not nome:
                        h = anuncio.find_elements(By.CSS_SELECTOR, 'h2, h3, .title')
                        if h:
                            nome = h[0].text.strip()
                            log_seminovos(f"Anuncio #{idx+1}: fallback nome='{nome[:60]}'")
                except Exception:
                    pass

                try:
                    if not imagem:
                        imgs = anuncio.find_elements(By.CSS_SELECTOR, 'img')
                        if imgs:
                            imagem = imgs[0].get_attribute('src') or imgs[0].get_attribute('data-src') or ''
                            log_seminovos(f"Anuncio #{idx+1}: fallback imagem found")
                except Exception:
                    pass

                try:
                    if not preco:
                        txt = anuncio.text
                        if 'R$' in txt:
                            for line in txt.splitlines():
                                if 'R$' in line:
                                    preco = line.strip(); break
                            log_seminovos(f"Anuncio #{idx+1}: fallback preco='{preco}'")
                except Exception:
                    pass

                try:
                    anchors = anuncio.find_elements(By.CSS_SELECTOR, 'a')
                    if anchors:
                        for a in anchors:
                            try:
                                href = a.get_attribute('href') or ''
                                if not href:
                                    continue
                                if href.strip().startswith('javascript:') or href.strip() == '#':
                                    continue
                                # normalize relative links
                                if href.startswith('/'):
                                    href_abs = base_url.rstrip('/') + href
                                else:
                                    href_abs = href
                                # ignore links that look like the listing page
                                if 'cidade[]' in href_abs or href_abs.rstrip('/') == url.rstrip('/') or href_abs.rstrip('/') == base_url.rstrip('/'):
                                    continue
                                link = href_abs
                                log_seminovos(f"Anuncio #{idx+1}: anchor link found='{link}'")
                                break
                            except Exception:
                                continue
                except Exception as e:
                    log_seminovos(f"Anuncio #{idx+1}: erro ao obter anchors: {e}")

                # If no valid anchor link, try JSON-LD url if it looks like a real detail url
                if not link:
                    try:
                        candidate = json_data.get('url', '') if isinstance(json_data, dict) else ''
                        if candidate:
                            if candidate.startswith('/'):
                                candidate_abs = base_url.rstrip('/') + candidate
                            else:
                                candidate_abs = candidate
                            if 'cidade[]' not in candidate_abs and candidate_abs.rstrip('/') != url.rstrip('/') and candidate_abs.rstrip('/') != base_url.rstrip('/'):
                                link = candidate_abs
                                log_seminovos(f"Anuncio #{idx+1}: usando JSON-LD url como fallback: {link}")
                            else:
                                log_seminovos(f"Anuncio #{idx+1}: JSON-LD url ignorado pois aponta para listagem: {candidate_abs}")
                    except Exception:
                        pass

                try:
                    local = ''
                    local_el = anuncio.find_elements(By.CSS_SELECTOR, '.localizacao, .location, .cidade')
                    if local_el:
                        local = local_el[0].text.strip()
                    else:
                        local = (cidade_raw.replace('-', ' ').title() if cidade_raw else "")
                except Exception:
                    local = (cidade_raw.replace('-', ' ').title() if cidade_raw else "")

                try:
                    localizacao_el = anuncio.find_element(By.CLASS_NAME, "localizacao")
                    local = localizacao_el.text.strip()
                except Exception:
                    local = (cidade_raw.replace('-', ' ').title() if cidade_raw else "")

                # Validação de filtros locais
                if ano_min is not None:
                    try:
                        import re
                        m = re.search(r"\b(19|20)\d{2}\b", nome)
                        if m and int(m.group(0)) < int(ano_min):
                            continue
                    except Exception:
                        pass
                if ano_max is not None:
                    try:
                        import re
                        m = re.search(r"\b(19|20)\d{2}\b", nome)
                        if m and int(m.group(0)) > int(ano_max):
                            continue
                    except Exception:
                        pass
                if preco_max is not None:
                    try:
                        if preco and int(str(preco).replace(".", "").replace(",", "")) > int(preco_max):
                            continue
                    except Exception:
                        pass
                if preco_min is not None:
                    try:
                        if preco and int(str(preco).replace(".", "").replace(",", "")) < int(preco_min):
                            continue
                    except Exception:
                        pass
                if km_max is not None or km_min is not None:
                    try:
                        if km:
                            vkm = int(''.join(ch for ch in str(km) if ch.isdigit()))
                            if km_min is not None and vkm < km_min:
                                continue
                            if km_max is not None and vkm > km_max:
                                continue
                    except Exception:
                        pass

                car_data = {
                    "Nome do Carro": nome,
                    "Valor": f"R$ {preco}" if str(preco) else "Preço não informado",
                    "KM": f"{km} km" if str(km) else "N/A",
                    "Localização": local,
                    "Imagem": imagem,
                    "Link": link,
                    "Portal": "Seminovos"
                }
                # keep original parsed JSON-LD available as fallback to avoid opening wrong links
                try:
                    car_data["_json_ld"] = json_data if isinstance(json_data, dict) and json_data else None
                except Exception:
                    car_data["_json_ld"] = None

                cars_to_process.append((car_data, link))

            except Exception as e:
                logar(f"[SEMINOVOS] Erro ao coletar anuncio: {e}")
                continue

        logar(f"[SEMINOVOS] FASE 1 conclu��da: {len(cars_to_process)} anúncios coletados")

        # FASE 2: Abrir página de detalhe para cada link coletado
        if filtros.get('capture_details', False):
            logar(f"[SEMINOVOS] FASE 2: Capturando detalhes de {len(cars_to_process)} anúncios...")
            for idx, (car_data, link) in enumerate(cars_to_process):
                if should_stop():
                    break
                try:
                    logar(f"[SEMINOVOS] Processando detalhe {idx+1}/{len(cars_to_process)}: {car_data.get('Nome do Carro', '')}")

                    details = None
                    # If the collected link looks like the listing page or is empty, try to use the JSON-LD parsed earlier
                    bad_link = False
                    try:
                        if not link or link.strip() == "":
                            bad_link = True
                        else:
                            # treat links that are the listing url or that contain the listing city segment as invalid detail links
                            if str(link).startswith(url) or 'cidade[]' in str(link):
                                bad_link = True
                    except Exception:
                        bad_link = False

                    if bad_link and car_data.get('_json_ld'):
                        log_seminovos(f"Link de detalhe possivelmente inválido ('{link}'), usando JSON-LD como fallback para '{car_data.get('Nome do Carro', '')}'")
                        try:
                            json_ld = car_data.get('_json_ld') or {}
                            details = {}
                            details['quilometragem'] = json_ld.get('mileageFromOdometer', {}).get('value', '') if isinstance(json_ld, dict) else ''
                            details['cambio'] = json_ld.get('vehicleTransmission', '') if isinstance(json_ld, dict) else ''
                            details['ano'] = json_ld.get('productionDate', '') if isinstance(json_ld, dict) else ''
                            details['portas'] = json_ld.get('numberOfDoors', '') if isinstance(json_ld, dict) else ''
                            details['combustivel'] = json_ld.get('fuelType', '') if isinstance(json_ld, dict) else ''
                            details['cor'] = json_ld.get('color', '') if isinstance(json_ld, dict) else ''
                            details['descricao'] = json_ld.get('description', '') if isinstance(json_ld, dict) else ''
                            details['palavrasProibidas'] = []
                        except Exception as e:
                            log_seminovos(f"Erro ao usar JSON-LD fallback: {e}")
                            details = None
                    else:
                        # If link looks valid, open detail page
                        if link:
                            details = extract_details_seminovos(driver, link, filtros.get('forbiddenWords', []))

                    if details:
                            quil = details.get("quilometragem", "")
                            camb = details.get("cambio", "")
                            ano_v = details.get("ano", "")
                            portas_v = details.get("portas", "")
                            combust_v = details.get("combustivel", "")
                            cor_v = details.get("cor", "")
                            desc_v = details.get("descricao", "")
                            palavras = details.get("palavrasProibidas", [])

                            car_data["Quilometragem"] = quil
                            car_data["quilometragem"] = quil
                            car_data["KM"] = car_data.get("KM") or quil

                            car_data["Cambio"] = camb
                            car_data["cambio"] = camb
                            car_data["Câmbio"] = camb

                            car_data["Ano"] = ano_v
                            car_data["Portas"] = portas_v
                            car_data["Combustivel"] = combust_v
                            car_data["combustivel"] = combust_v

                            car_data["Cor"] = cor_v
                            car_data["Descricao"] = desc_v
                            car_data["descricao"] = desc_v
                            car_data["Descrição"] = desc_v

                            car_data["Palavras Proibidas"] = palavras
                            car_data["palavrasProibidas"] = palavras

                            if palavras:
                                logar(f"[SEMINOVOS] Palavras proibidas encontradas: {car_data.get('Nome do Carro', '')} - {palavras}")

                    add_dado(car_data)
                except Exception as e:
                    logar(f"[SEMINOVOS] Erro ao processar detalhe: {e}")
                    add_dado(car_data)
                    continue
            logar(f"[SEMINOVOS] FASE 2 concluída")
        else:
            # Se não capturar detalhes, apenas adiciona os dados básicos
            for car_data, _ in cars_to_process:
                add_dado(car_data)

        driver.quit()

    except Exception as e:
        logar(f"[ERRO] Seminovos - Erro: {str(e)}")

def scraping_localiza(filtros):
    try:
        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            global current_driver
            current_driver = driver
        except Exception:
            pass

        # Cidade padrao: mg-belo-horizonte
        cidade_uf = filtros.get("cidadeUf", filtros.get("cidade_uf", "mg-belo-horizonte")).lower()
        page = 1
        encontrados_total = 0

        while not should_stop():
            extra = []
            ano_min = filtros.get("anoMin", filtros.get("ano_min", None))
            ano_max = filtros.get("anoMax", filtros.get("ano_max", None))
            preco_min = filtros.get("precoMin", filtros.get("preco_min", None))
            preco_max = filtros.get("precoMax", filtros.get("preco_max", None))
            marca_slug = slugify(filtros.get('marca') or '')
            modelo_slug = slugify(filtros.get('modelo') or '')
            carroceria_slug = slugify(filtros.get('carroceria') or '')
            km_max = filtros.get('kmMax') or filtros.get('km_max') or None
            if ano_min: extra.append(f"anoDe={ano_min}")
            if ano_max: extra.append(f"anoAte={ano_max}")
            if preco_min: extra.append(f"PrecoDe={preco_min}")
            if preco_max: extra.append(f"PrecoAte={preco_max}")
            if carroceria_slug: extra.append(f"categorias={carroceria_slug}")
            if km_max: extra.append(f"kmAte={km_max}")
            q = ("&"+"&".join(extra)) if extra else ""

            path_suffix = ''
            if marca_slug and modelo_slug:
                path_suffix = f"/{marca_slug}/{modelo_slug}"
            elif marca_slug:
                path_suffix = f"/{marca_slug}"

            url = f"https://seminovos.localiza.com/carros/{cidade_uf}{path_suffix}?page={page}{q}"
            logar(f"[LOCALIZA] Acessando: {url}")
            driver.get(url)
            time.sleep(3)

            cards = driver.find_elements(By.CSS_SELECTOR, '[data-testid="product-card-standard"], .mui-1fobd63, .product-card')
            if not cards:
                logar("[LOCALIZA] Nenhum card encontrado. Encerrando.")
                break

            for card in cards:
                if should_stop():
                    break
                try:
                    # Nome
                    nome = ""
                    nome_el = None
                    for sel in ["h2", "[data-testid='product-card-title']", ".name-vehicle", ".title"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            nome_el = els[0]
                            break
                    if nome_el:
                        nome = nome_el.text.replace("\n", " ").strip()

                    # Imagem
                    imagem = ""
                    img_el = None
                    for sel in ["img", "picture img"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            img_el = els[0]
                            break
                    if img_el:
                        imagem = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""

                    # Valor
                    valor = ""
                    preco_el = None
                    for sel in ["h3", "[data-testid='product-card-price']", ".price-vehicle"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            preco_el = els[0]
                            break
                    if preco_el:
                        valor = preco_el.text.split("\n")[0].strip()

                    # KM
                    km = "N/A"
                    detalhes = card.find_elements(By.CSS_SELECTOR, ".mui-rsig1c, .details, [class*='km'], li")
                    if detalhes:
                        try:
                            km = detalhes[0].text.strip()
                        except:
                            pass

                    # Local
                    local = ""
                    local_el = None
                    for sel in [".mui-12ksvqc", "[class*='location']", ".details"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            local_el = els[0]
                            break
                    if local_el:
                        local = local_el.text.strip()

                    # Link
                    link = ""
                    a_el = None
                    for sel in ["a", "[data-testid='product-card-anchor']"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            a_el = els[0]
                            break
                    if a_el:
                        href = a_el.get_attribute("href") or a_el.get_attribute("data-href") or ""
                        if href and href.startswith("/"):
                            href = "https://seminovos.localiza.com" + href
                        link = href

                    if nome:
                        add_dado({
                            "Nome do Carro": nome,
                            "Valor": valor,
                            "KM": km,
                            "Localização": local,
                            "Imagem": imagem,
                            "Link": link,
                            "Portal": "Localiza"
                        })
                        encontrados_total += 1
                except Exception:
                    continue

            # Tentativa de proxima pagina
            page += 1
            if page > 20:
                break

        logar(f"[LOCALIZA] Coletados {encontrados_total} itens.")
        driver.quit()
    except Exception as e:
        logar(f"[ERRO] Localiza - Erro: {str(e)}")


def scraping_unidas(filtros):
    try:
        options = Options()
        options.headless = True
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        driver = webdriver.Firefox(service=Service(), options=options)
        try:
            global current_driver
            current_driver = driver
        except Exception:
            pass

        page = 1
        encontrados_total = 0

        # Build filter path according to provided filters
        marca_slug = slugify(filtros.get('marca') or '')
        modelo_slug = slugify(filtros.get('modelo') or '')
        carroceria_slug = slugify(filtros.get('carroceria') or '')

        # Normalize numeric filters (try several possible keys)
        def _num(key_variants, default=None):
            for k in key_variants:
                v = filtros.get(k)
                if v is not None and v != "":
                    try:
                        return int(v)
                    except Exception:
                        try:
                            return int(str(v).replace('.', '').replace(',', ''))
                        except Exception:
                            return None
            return default

        price_min = _num(['precoMin', 'preco_min', 'precoMin'])
        price_max = _num(['precoMax', 'preco_max', 'precoMax'])
        km_min = _num(['kmMin', 'km_min', 'kmMin'])
        km_max = _num(['kmMax', 'km_max', 'kmMax'])
        ano_min = _num(['anoMin', 'ano_min', 'anoMin'])
        ano_max = _num(['anoMax', 'ano_max', 'anoMax'])

        # Build path prioritizing filters so they apply even without carroceria
        path_parts: list[str] = []

        # Prices first
        if price_min is not None:
            path_parts.append(f"valorini-{price_min}")
        if price_max is not None:
            path_parts.append(f"valorfim-{price_max}")

        # KM next
        if km_min is not None:
            path_parts.append(f"kmini-{km_min}")
        if km_max is not None:
            path_parts.append(f"kmfim-{km_max}")

        # Year: if only min provided, mirror to anofim as well
        if ano_min is not None and (ano_max is None):
            ano_max = ano_min
        if ano_min is not None:
            path_parts.append(f"anoini-{ano_min}")
        if ano_max is not None:
            path_parts.append(f"anofim-{ano_max}")

        # Then optional brand/model
        if marca_slug:
            path_parts.append(marca_slug)
        if modelo_slug:
            path_parts.append(modelo_slug)

        # Optional location: avoid adding default BH to keep generic filtering working
        cidade_raw = filtros.get('cidade') or filtros.get('cidade_id') or filtros.get('cidadeId') or ''
        if cidade_raw:
            slug_cidade = slugify(cidade_raw)
            if slug_cidade and slug_cidade not in ['belo-horizonte', 'bh']:
                city_unidas_mapping = {
                    'contagem': 'contagem-contagem-mg',
                    'contagem-mg': 'contagem-contagem-mg',
                    'betim': 'betim-betim-mg',
                    'betim-mg': 'betim-betim-mg'
                }
                path_parts.append(city_unidas_mapping.get(slug_cidade, slug_cidade))

        # Finally carroceria if provided
        if carroceria_slug:
            path_parts.append(f"categoria-{carroceria_slug}")

        base_path = "/".join(path_parts)
        if base_path:
            base_prefix = f"https://seminovos.unidas.com.br/veiculos/{base_path}"
        else:
            base_prefix = "https://seminovos.unidas.com.br/veiculos"

        # Ensure base_prefix does not end with a trailing slash (so query params start with ?)
        if base_prefix.endswith('/'):
            base_prefix = base_prefix.rstrip('/')

        while not should_stop():
            url = f"{base_prefix}?page={page}&perpage=24&order=destaque:desc&layout=grid"
            logar(f"[UNIDAS] Acessando: {url}")
            driver.get(url)
            time.sleep(3)

            # Fecha possiveis modais de geolocalizacao
            try:
                modals = driver.find_elements(By.CLASS_NAME, "geo-modal-close")
                for modal in modals:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", modal)
                        time.sleep(0.5)
                        modal.click()
                        time.sleep(0.5)
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", modal)
                        except Exception:
                            pass
            except Exception:
                pass

            cidade_raw = filtros.get('cidade') or filtros.get('cidade_id') or filtros.get('cidadeId') or ''
            if cidade_raw:
                logar(f"[UNIDAS] Filtrando por cidade: {cidade_raw}")
                try:
                    # click the location input to open modal
                    clicked = False
                    for sel in ['.location-input', '.location-content', '.container-input-geo', '.location-selected']:
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, sel)
                            driver.execute_script("arguments[0].scrollIntoView(true);", el)
                            time.sleep(0.3)
                            el.click()
                            clicked = True
                            time.sleep(0.6)
                            break
                        except Exception:
                            continue

                    # If modal opened, find the input and type city, then confirm
                    if clicked:
                        try:
                            # normalize city text for typing
                            city_to_type = str(cidade_raw)
                            inp = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.ID, 'geo-city-select')))
                            driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input'))", inp)
                            time.sleep(0.2)
                            inp.send_keys(city_to_type)
                            time.sleep(1.0)
                            # trigger input event via JS to notify frameworks
                            driver.execute_script("var e = new Event('input', {bubbles:true}); document.getElementById('geo-city-select').dispatchEvent(e);")
                            time.sleep(1.0)

                            # Try to select first suggestion if appears
                            try:
                                first_sugg = driver.find_element(By.CSS_SELECTOR, '.geo-modal-list li, .suggestion-item, .vue-geo-suggestion')
                                driver.execute_script("arguments[0].scrollIntoView(true);", first_sugg)
                                time.sleep(0.2)
                                first_sugg.click()
                                time.sleep(0.4)
                            except Exception:
                                # fallback: press Enter
                                try:
                                    inp.send_keys('\n')
                                    time.sleep(0.5)
                                except Exception:
                                    pass

                            # click confirm button if enabled
                            try:
                                confirm = driver.find_element(By.CSS_SELECTOR, '.geo-modal-button-confirm')
                                if confirm and confirm.is_enabled():
                                    driver.execute_script("arguments[0].click();", confirm)
                                    time.sleep(0.8)
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    pass

            # Track count before processing page to detect no-new-results condition
            before_count = encontrados_total

            cards = driver.find_elements(By.CSS_SELECTOR, ".new-card, .card, [class*='vehicle-card']")
            if not cards:
                logar("[UNIDAS] Nenhum card encontrado nesta página. Encerrando.")
                break

            for card in cards:
                if should_stop():
                    break
                try:
                    # Nome (junta nome + info)
                    nome = ""
                    nome_parts = []
                    for sel in [".name-vehicle", ".info-vehicle", "h2", "h3", ".title", ".vehicle-name"]:
                        for el in card.find_elements(By.CSS_SELECTOR, sel):
                            t = el.text.strip()
                            if t and t not in nome_parts:
                                nome_parts.append(t)
                    nome = (" ".join(nome_parts)).strip()

                    # Imagem
                    imagem = ""
                    img_el = None
                    for sel in [".card-image", "img", "picture img"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            img_el = els[0]
                            break
                    if img_el:
                        imagem = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""

                    # Valor
                    valor = ""
                    preco_el = None
                    for sel in [".price-vehicle", "[class*='price']", "h3", "strong.price"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            preco_el = els[0]
                            break
                    if preco_el:
                        valor = preco_el.text.strip()

                    # KM e Local
                    km = "N/A"
                    local = ""
                    details = card.find_elements(By.CSS_SELECTOR, ".details, li, [class*='km'], [class*='local']")
                    if details:
                        try:
                            if len(details) > 1:
                                km = details[1].text.strip() or km
                                local = details[0].text.strip() or local
                            else:
                                for d in details:
                                    txt = d.text.lower()
                                    if 'km' in txt:
                                        km = d.text.strip()
                                    if any(k in txt for k in ['bh', 'belo', 'mg']):
                                        local = d.text.strip()
                        except:
                            pass

                    # Link
                    link = ""
                    a_el = None
                    for sel in ["a", "[data-testid='card-link']"]:
                        els = card.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            a_el = els[0]
                            break
                    if a_el:
                        link = a_el.get_attribute("href") or ""

                    if nome:
                        add_dado({
                            "Nome do Carro": nome,
                            "Valor": valor,
                            "KM": km,
                            "Localização": local,
                            "Imagem": imagem,
                            "Link": link,
                            "Portal": "Unidas"
                        })
                        encontrados_total += 1
                except Exception:
                    continue

            # If this page did not add any new items, stop to avoid infinite loops
            if encontrados_total == before_count:
                logar("[UNIDAS] Nenhum novo anúncio coletado nesta página. Encerrando.")
                break

            page += 1
            if page > 40:
                break

        logar(f"[UNIDAS] Coletados {encontrados_total} itens.")
        driver.quit()
    except Exception as e:
        logar(f"[ERRO] Unidas - Erro: {str(e)}")


def executar_scraping(filtros_json):
    global dados_carros, parar_scraping
    dados_carros = []
    parar_scraping = False

    try:
        logar(f"[DEBUG] JSON recebido: {repr(filtros_json)}")
        logar(f"[DEBUG] Tamanho: {len(filtros_json)} caracteres")

        if not filtros_json or filtros_json.strip() == '':
            logar("[ERRO] JSON vazio recebido")
            return json.dumps([])

        try:
            filtros = json.loads(filtros_json)
        except Exception:
            # Accept Python-style dict literals (e.g. {key: value}) as fallback using ast.literal_eval
            try:
                import ast
                filtros = ast.literal_eval(filtros_json)
                # Convert booleans/None to JSON-like types if needed
            except Exception as e:
                logar(f"[ERRO] Falha ao parsear filtros JSON: {e}")
                raise

        logar("[INICIO] Iniciando scraping de carros...")

        allowed = set()
        portals = filtros.get('portals')
        if isinstance(portals, list) and portals:
            try:
                allowed = set([str(p) for p in portals])
            except Exception:
                allowed = set()

        def can(p: str) -> bool:
            return (len(allowed) == 0) or (p in allowed)

        if can('OLX'):
            scraping_olx(filtros)
        if can('Webmotors'):
            scraping_webmotors(filtros)
        if can('Mercado Livre'):
            scraping_mercado_livre(filtros)
        if can('Seminovos'):
            scraping_seminovos(filtros)
        if can('Localiza'):
            scraping_localiza(filtros)
        if can('Unidas'):
            scraping_unidas(filtros)

        if dados_carros:
            df = pd.DataFrame(dados_carros)
            df.to_excel("anuncios_carros.xlsx", index=False)
            logar(f"[OK] Planilha 'anuncios_carros.xlsx' gerada com {len(dados_carros)} carros.")
            try:
                print("EVENT_EXCEL_SAVED:anuncios_carros.xlsx")
                sys.stdout.flush()
            except Exception:
                pass

            return json.dumps(dados_carros, ensure_ascii=False)
        else:
            logar("[AVISO] Nenhum carro encontrado.")
            return json.dumps([])

    except Exception as e:
        logar(f"[ERRO] Erro geral: {str(e)}")
        return json.dumps([])

if __name__ == "__main__":
    SEMINOVOS_VERBOSE = False
    if len(sys.argv) > 1:
        filtros_json = sys.argv[1]
        flags = sys.argv[2:]
        if any(f in ['--verbose-seminovos', '--verbose', '-v'] for f in flags):
            SEMINOVOS_VERBOSE = True
            logar("[DEBUG] Seminovos verbose logging ativado via argumentos")
        resultado = executar_scraping(filtros_json)
        print("RESULTADO_JSON:" + resultado)
    else:
        logar("[ERRO] Uso: python car_scraper.py '{\"ano_min\": 2014, \"preco_max\": 20000}' [--verbose-seminovos]")



# ============================================================================
# WRAPPER MAIN_SCRAPER
# ============================================================================

def main_scraper(filtros):
    """
    Função principal de scraping
    """
    try:
        logar("=== INICIANDO SCRAPING ===")
        portals = filtros.get("portals", [])

        if "OLX" in portals:
            logar("Scraping OLX...")
            try:
                scraping_olx(filtros)
            except Exception as e:
                logar(f"Erro OLX: {e}")

        logar("=== CONCLUÍDO ===")
    except Exception as e:
        logar(f"ERRO: {e}")
        import traceback
        traceback.print_exc()



# ============================================================================
# INTERFACE FLET E APLICAÇÃO PRINCIPAL  
# ============================================================================

import flet as ft
import subprocess
import sys
import threading
import json
import os
import time
from typing import List, Dict, Any, Optional

try:
    import pandas as pd
except Exception:
    pd = None

# Scraper integrado
# Executa próprio arquivo
STOP_SIGNAL_PATH = os.path.join(os.getcwd(), "STOP_SIGopen_linkNAL.txt")
STATE_FILE = os.path.join(os.getcwd(), "app_state.json")

# Utilities

def write_stop_signal():
    try:
        with open(STOP_SIGNAL_PATH, "w", encoding="utf-8") as f:
            f.write("stop")
    except Exception as e:
        print("Failed to write stop signal:", e)


def remove_stop_signal():
    try:
        if os.path.exists(STOP_SIGNAL_PATH):
            os.remove(STOP_SIGNAL_PATH)
    except Exception:
        pass


def save_app_state(state_data: Dict[str, Any]):
    """Save app state to JSON file"""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar estado: {e}")


def load_app_state() -> Dict[str, Any]:
    """Load app state from JSON file"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar estado: {e}")
    return {}


class ScraperApp:
    
    def __init__(self, page: ft.Page):
        self.page = page
        self.child: Optional[subprocess.Popen] = None
        self.results: List[Dict[str, Any]] = []
        self.filtered_results: List[Dict[str, Any]] = []
        self.liked_items: set = set()
        self.hidden_items: set = set()
        self.ranking_list: List[str] = []  # Lista ordenada de links dos carros curtidos
        self.ranking_descriptions: Dict[str, str] = {}  # Descrições por posição no ranking
        self.liked_items_cache: Dict[str, Dict[str, Any]] = {}  # Cache completo dos carros curtidos
        self.scraping_speed: str = "baixo"  # Velocidade do scraping: baixo, medio, rapido
        self.preferences: Dict[str, Any] = {
            'quilometragem': 4,
            'potenciaMotor': 3,
            'portas': 2,
            'ano': 1,
        }
        self.preference_order: List[str] = ['quilometragem', 'potenciaMotor', 'portas', 'ano']  # Ordem das preferências
        self.best_match_link: Optional[str] = None  # Link do carro melhor correspondente

        self.ranking_list: List[str] = []
        self.ranking_descriptions: Dict[str, str] = {}
        self.preferences: Dict[str, Any] = {
            'quilometragem': 4,
            'potenciaMotor': 3,
            'portas': 2,
            'ano': 1,
        }
        self.best_match_link: Optional[str] = None
        self.load_state()
        self.create_ui()
        remove_stop_signal()

    def _build_preference_item(self, key: str, label: str, value: int, position: int, rebuild_callback) -> ft.Container:
        """Build preference list item with reorder controls"""
        
        def move_up(e):
            if position > 0:
                self.preference_order[position], self.preference_order[position-1] = \
                    self.preference_order[position-1], self.preference_order[position]
                rebuild_callback()
        
        def move_down(e):
            if position < len(self.preference_order) - 1:
                self.preference_order[position], self.preference_order[position+1] = \
                    self.preference_order[position+1], self.preference_order[position]
                rebuild_callback()
        
        return ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Text(f"{position + 1}", size=16, weight="bold"),
                    width=30,
                    alignment=ft.alignment.center,
                ),
                ft.Text(label, size=14, expand=True),
                ft.Container(
                    content=ft.Text(f"Peso: {value}", size=12, color="#424242"),
                    padding=4,
                    bgcolor="#f3f4f6",
                    border_radius=4,
                ),
                ft.Column([
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_ARROW_UP,
                        icon_size=20,
                        tooltip="Mover para cima (aumentar prioridade)",
                        on_click=move_up,
                        disabled=(position == 0)
                    ),
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                        icon_size=20,
                        tooltip="Mover para baixo (diminuir prioridade)",
                        on_click=move_down,
                        disabled=(position == len(self.preference_order) - 1)
                    ),
                ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=12,
            border=ft.border.all(1, "#e5e7eb"),
            border_radius=8,
            bgcolor="white",
        )


    def create_ui(self):
        p = self.page
        p.title = "MelhorCarro - Flet"
        p.window_width = 1400
        p.window_height = 900

##        # Loading screen elements
##        self.loading_visible = False
##        self.cars_found = ft.Text("0", size=28, weight="bold", color="#2563EB")
##        self.elapsed_time = ft.Text("⏱️ Tempo: 0s", size=12, color="#424242")
##        self.loading_logs_column = ft.Column(
##            controls=[],
##            scroll=ft.ScrollMode.AUTO,
##            expand=True,
##        )
##        self.loading_logs = ft.Container(
##            content=self.loading_logs_column,
##            height=200,
##            bgcolor="#1f2937",
##            padding=12,
##            border_radius=8,
##        )
##        self.loading_overlay = ft.Container(
##            content=ft.Column([
##                ft.Container(height=20),
##                ft.Column([
##                    ft.Container(
##                        content=ft.Column([
##                            ft.Text("🔄", size=48),
##                        ], alignment=ft.MainAxisAlignment.CENTER),
##                        height=100,
##                        width=100,
##                        alignment=ft.alignment.center,
##                    ),
##                    ft.Text("🔍 Coletando Carros", size=24, weight="bold", text_align=ft.TextAlign.CENTER),
##                    ft.Row([
##                        ft.Text("Encontrados: ", size=14),
##                        self.cars_found,
##                        ft.Text(" carros", size=14),
##                    ], alignment=ft.MainAxisAlignment.CENTER),
##                    self.elapsed_time,
##                    ft.Container(height=10),
##                    ft.Container(
##                        content=ft.Column([
##                            ft.Text("💡 Dicas Úteis:", size=12, weight="bold", color="#1e40af"),
##                            ft.Text("✓ Verifique se os filtros estão corretos", size=11, color="#1e40af"),
##                            ft.Text("✓ A busca pode levar alguns minutos", size=11, color="#1e40af"),
##                            ft.Text("✓ Resultados serão filtrados pelo preço máximo", size=11, color="#1e40af"),
##                            ft.Text("✓ Use o botão 'Parar' para interromper", size=11, color="#1e40af"),
##                        ], spacing=4),
##                        padding=12,
##                        bgcolor="#eff6ff",
##                        border_radius=8,
##                        border="1px solid #bfdbfe",
##                    ),
##                    ft.Container(height=10),
##                    ft.Container(
##                        content=ft.Column([
##                            ft.Text("🔄 O que está acontecendo:", size=12, weight="bold", color="#92400e"),
##                            ft.Text("• Varrendo múltiplos portais de vendas", size=11, color="#92400e"),
##                            ft.Text("• Aplicando seus filtros (preço, ano, km, etc)", size=11, color="#92400e"),
##                            ft.Text("• Removendo duplicatas", size=11, color="#92400e"),
##                            ft.Text("• Compilando resultados...", size=11, color="#92400e"),
##                        ], spacing=4),
##                        padding=12,
##                        bgcolor="#fef3c7",
##                        border_radius=8,
##                        border="1px solid #fcd34d",
##                    ),
##                    ft.Container(height=10),
##                    ft.Text("📋 Log Detalhado:", size=12, weight="bold", color="#ddd"),
##                    self.loading_logs,
##                ], spacing=8, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER, width=600),
##            ], alignment=ft.MainAxisAlignment.CENTER),
##            bgcolor="rgba(0, 0, 0, 0.9)",
##            expand=True,
##            visible=False,
##        )

        # Filters column
        self.cidade = ft.TextField(label="Localização (cidade)", value="belo-horizonte", width=280)
        self.ano_min = ft.TextField(label="Ano Mínimo", value="2014", width=140)
        self.ano_max = ft.TextField(label="Ano M��ximo", value="2025", width=140)
        self.preco_min = ft.TextField(label="Preço Mínimo (R$)", value="0", width=140)
        self.preco_max = ft.TextField(label="Preço Máximo (R$)", value="20000", width=140)
        self.km_min = ft.TextField(label="KM Mínimo", value="", width=140)
        self.km_max = ft.TextField(label="KM Máximo", value="", width=140)

        self.marca = ft.TextField(label="Marca", value="", width=280)
        self.modelo = ft.TextField(label="Modelo", value="", width=280)
        self.carroceria = ft.TextField(label="Carroceria", value="", width=280)

        self.combustivel = ft.TextField(label="Combustível", value="", width=200)
        self.portas = ft.TextField(label="Portas", value="", width=120)
        self.transmissao = ft.TextField(label="Transmissão", value="", width=160)
        self.cor = ft.TextField(label="Cor", value="", width=160)
        self.motor = ft.TextField(label="Motor", value="", width=160)
        self.direcao = ft.TextField(label="Direção", value="", width=160)
        self.tracao = ft.TextField(label="Tração", value="", width=160)
        self.aceita_troca = ft.Checkbox(label="Aceita troca", value=False)
        self.blindado = ft.Checkbox(label="Blindado", value=False)
        self.versao = ft.TextField(label="Versão", value="", width=200)
        self.tipo_veiculo = ft.TextField(label="Tipo de Veículo", value="", width=200)
        self.situacao = ft.TextField(label="Situação", value="", width=200)

        # Portals
        self.portal_olx = ft.Checkbox(label="OLX", value=True)
        self.portal_webmotors = ft.Checkbox(label="Webmotors", value=True)
        self.portal_ml = ft.Checkbox(label="Mercado Livre", value=True)
        self.portal_seminovos = ft.Checkbox(label="Seminovos", value=True)
        self.portal_localiza = ft.Checkbox(label="Localiza", value=False)
        self.portal_unidas = ft.Checkbox(label="Unidas", value=False)

        self.capture_details = ft.Checkbox(label="Capturar detalhes", value=True)
        self.capture_details.tooltip = "Abrir páginas de detalhe para extrair informações adicionais"

        self.forbidden = ft.TextField(label="Palavras proibidas (vírgula-separadas)", value="", width=360)
        self.zenrows_key = ft.TextField(label="ZenRows API Key (opcional)", value="", width=360)

        # Controls
        self.start_btn = ft.ElevatedButton("Iniciar Scraping", on_click=self.on_start)
        self.stop_btn = ft.ElevatedButton("Parar Scraping", on_click=self.on_stop, bgcolor=ft.Colors.RED)
        self.stop_btn.disabled = True
        self.import_btn = ft.ElevatedButton("Importar Excel/CSV", on_click=self.on_import)
        self.export_btn = ft.ElevatedButton("Exportar Excel", on_click=self.on_export)
        self.export_btn.disabled = True

        # Results controls
        self.search_field = ft.TextField(label="Pesquisar nos resultados", value="", width=600, on_change=self._on_search_change)
        self.sort_dropdown = ft.Dropdown(width=200, label="Ordenar por", options=[
            ft.dropdown.Option("Nome"),
            ft.dropdown.Option("Preço: Menor para Maior"),
            ft.dropdown.Option("Preço: Maior para Menor"),
            ft.dropdown.Option("KM: Menor para Maior"),
            ft.dropdown.Option("KM: Maior para Menor"),
            ft.dropdown.Option("Ano: Mais Novo"),
            ft.dropdown.Option("Ano: Mais Antigo"),
            ft.dropdown.Option("Curtidos"),
        ], on_change=self._on_sort_change)
        self.sort_dropdown.value = "Nome"
        self.preferences_btn = ft.ElevatedButton("Suas Preferências", on_click=self._on_preferences_click)
        self.ranking_btn = ft.ElevatedButton("Ranking", on_click=self._on_ranking_click)
        self.favorites_btn = ft.ElevatedButton("Favoritos", on_click=self._on_favorites_click)



        # Logs and results
        self.log_area = ft.Text(value="", selectable=True)
        self.results_view = ft.Column(controls=[], scroll=ft.ScrollMode.AUTO, spacing=8, expand=True)

        left_col = ft.Column([ft.Text("Filtros de Busca", style="headlineSmall"),
                              self.cidade,
                              ft.Row([self.ano_min, self.ano_max]),
                              ft.Row([self.preco_min, self.preco_max]),
                              ft.Row([self.km_min, self.km_max]),
                              self.marca,
                              self.modelo,
                              self.carroceria,
                              ft.Row([self.combustivel, self.portas, self.transmissao]),
                              ft.Row([self.cor, self.motor, self.direcao]),
                              ft.Row([self.tracao, self.versao, self.tipo_veiculo]),
                              ft.Row([self.aceita_troca, self.blindado]),
                              self.situacao,
                              ft.Divider(),
                              ft.Text("Portais"),
                              ft.Row([self.portal_olx, self.portal_webmotors, self.portal_ml]),
                              ft.Row([self.portal_seminovos, self.portal_localiza, self.portal_unidas]),
                              ft.Divider(),
                              self.capture_details,
                              self.forbidden,
                              self.zenrows_key,
                              ft.Row([self.start_btn, self.stop_btn]),
                              ft.Row([self.import_btn, self.export_btn]),
                              ], scroll=ft.ScrollMode.AUTO, width=340)

        # Results column with search and sort controls
        results_header = ft.Column([
            ft.Row([ft.Container(content=ft.Image(src='data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="%231565c0" d="M3 11c0-1.1.9-2 2-2h1.2l1.6-3.2C8.1 4 8.5 3.8 8.9 3.8H15c.4 0 .8.2 1.1.4L17.7 9H19c1.1 0 2 .9 2 2v2c0 .6-.4 1-1 1h-1c-.6 0-1-.4-1-1v-1H7v1c0 .6-.4 1-1 1H5c-.6 0-1-.4-1-1v-2zM6.5 17c-.8 0-1.5.7-1.5 1.5S5.7 20 6.5 20s1.5-.7 1.5-1.5S7.3 17 6.5 17zm11 0c-.8 0-1.5.7-1.5 1.5S16.7 20 17.5 20s1.5-.7 1.5-1.5S18.3 17 17.5 17z"/></svg>'), bgcolor="#2563EB", padding=6, border_radius=6), ft.Text("MelhorCarro", style="titleMedium")], spacing=10),
            ft.Text("Resultados", style="headlineSmall"),
            ft.Row([
                self.search_field,
                self.sort_dropdown,
            ], wrap=True),
            ft.Row([
                self.preferences_btn,
                self.ranking_btn,
                self.favorites_btn,
            ], spacing=10),
        ], spacing=8)

        right_col = ft.Column([
            results_header,
            self.results_view,
            ft.Divider(),
            ft.Text("Logs:", size=12),
            ft.Container(content=self.log_area, padding=8, bgcolor="#f5f5f5", height=60)
        ], expand=True)

        # Settings tab content
        self.speed_radio = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(value="baixo", label="Baixo - Mais seguro, menos detecção (padrão)"),
                ft.Radio(value="medio", label="Médio - Balanceado entre velocidade e segurança"),
                ft.Radio(value="rapido", label="Rápido - Máxima velocidade (maior risco de bloqueio)"),
            ]),
            value=self.scraping_speed,
            on_change=self.on_speed_change
        )

        settings_content = ft.Column([
            ft.Text("Configurações de Scraping", style="headlineSmall"),
            ft.Divider(),
            ft.Text("Velocidade do Scraping", size=16, weight="bold"),
            ft.Text(
                "Escolha a velocidade de coleta de dados. Velocidades mais altas podem ser bloqueadas pelos sites.",
                size=12,
                color="#424242",
                italic=True
            ),
            ft.Container(height=10),
            self.speed_radio,
            ft.Container(height=20),
            ft.Container(
                content=ft.Column([
                    ft.Text("ℹ️ Informações sobre as velocidades:", size=14, weight="bold"),
                    ft.Text("• Baixo: ~3-5 segundos entre requisições", size=12),
                    ft.Text("• Médio: ~1-2 segundos entre requisições", size=12),
                    ft.Text("• Rápido: ~0.5-1 segundo entre requisições", size=12),
                ], spacing=8),
                padding=12,
                bgcolor="#eff6ff",
                border_radius=8,
                border=ft.border.all(1, "#bfdbfe")
            ),
        ], scroll=ft.ScrollMode.AUTO, expand=True)

        # Create tabs
        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(
                    text="Buscar",
                    icon=ft.Icons.SEARCH,
                    content=ft.Row([left_col, ft.VerticalDivider(), right_col], expand=True)
                ),
                ft.Tab(
                    text="Configurações",
                    icon=ft.Icons.SETTINGS,
                    content=ft.Container(
                        content=settings_content,
                        padding=20,
                        expand=True
                    )
                ),
            ],
            expand=True,
        )

        # Main layout with loading overlay
        main_content = ft.Stack([
            tabs,
            #self.loading_overlay,
        ], expand=True)

        p.add(main_content)

    def append_log(self, msg: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_area.value = (self.log_area.value + f"[{timestamp}] {msg}\n")[-20000:]
        self.page.update()

    def on_speed_change(self, e):
        self.scraping_speed = e.control.value
        self.save_state()
        self.append_log(f"Velocidade alterada para: {self.scraping_speed}")

    def show_loading_screen(self):
        # enable and show loading overlay
        self.loading_visible = True
        self.loading_start_time = time.time()
        try:
            self.loading_overlay.visible = True
        except Exception:
            pass
        #self.loading_logs_column.controls.clear()
        #self.cars_found.value = "0"
        self.page.update()

    def hide_loading_screen(self):
        self.loading_visible = False
        try:
            # hide and collapse overlay to avoid covering UI
            self.loading_overlay.visible = False
            try:
                self.loading_overlay.content = ft.Container()
            except Exception:
                pass
            try:
                self.loading_overlay.bgcolor = None
            except Exception:
                pass
            try:
                self.loading_overlay.expand = False
            except Exception:
                pass
        except Exception:
            pass
        self.page.update()

##    def update_loading_stats(self, cars_count: int):
##        self.cars_found.value = str(cars_count)
##        if hasattr(self, 'loading_start_time'):
##            elapsed = int(time.time() - self.loading_start_time)
##            self.elapsed_time.value = f"⏱️ Tempo: {elapsed}s"
##        self.page.update()

    def add_loading_log(self, msg: str):
        if self.loading_visible:
            log_text = ft.Text(
                f"• {msg}",
                size=10,
                color="#ddd",
                selectable=True,
            )
            #self.loading_logs_column.controls.append(log_text)
##            if len(self.loading_logs_column.controls) > 100:
##                self.loading_logs_column.controls.pop(0)
##            self.loading_logs_column.scroll_to(offset=-1, duration=100)
            self.page.update()

    def show_image_dialog(self, image_url: str, title: str = "Imagem"):
        try:
            img = ft.Image(src=image_url, width=900, height=600, fit=ft.ImageFit.CONTAIN)
            dlg = ft.AlertDialog(content=ft.Column([ft.Text(title), img], tight=True), actions=[ft.TextButton("Fechar", on_click=lambda e: self._close_dialog())], modal=True)
            self._current_dialog = dlg
            try:
                self.page.dialog = None
            except Exception:
                pass
            self._open_alert_dialog(dlg, "Imagem")
        except Exception as e:
            self.append_log(f"Erro ao abrir imagem: {e}")

    # ============================================================================
    # MÉTODOS DE DIÁLOGO - ADICIONE/SUBSTITUA ESTES MÉTODOS NA CLASSE ScraperApp
    # ============================================================================


    def _open_alert_dialog(self, dlg: ft.AlertDialog, name: str = None):
        """Open a dialog using Flet's correct API."""
        try:
            if name:
                self.append_log(f"Abrindo diálogo: {name}")
        except Exception:
            pass
        
        try:
            # Hide loading overlay
            try:
                self.loading_visible = False
                self.loading_overlay.visible = False
            except Exception:
                pass
            
            # Open dialog using page.open()
            self.page.open(dlg)
            self.append_log(f"Diálogo '{name}' aberto com sucesso")
        except Exception as e:
            self.append_log(f"Erro ao abrir diálogo {name or ''}: {e}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")

    def _close_dialog(self, e=None):
        """Close current dialog."""
        try:
            if hasattr(self, '_current_dialog') and self._current_dialog:
                self.page.close(self._current_dialog)
                self._current_dialog = None
            self.page.update()
        except Exception as ex:
            self.append_log(f"Erro ao fechar diálogo: {ex}")

        def _build_preference_item(self, key: str, label: str, value: int, position: int, rebuild_callback) -> ft.Container:
            def move_up(e):
                if position > 0:
                    self.preference_order[position], self.preference_order[position-1] = \
                        self.preference_order[position-1], self.preference_order[position]
                    rebuild_callback()
            def move_down(e):
                if position < len(self.preference_order) - 1:
                    self.preference_order[position], self.preference_order[position+1] = \
                        self.preference_order[position+1], self.preference_order[position]
                    rebuild_callback()
            return ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(f"{position + 1}", size=16, weight="bold"),
                        width=30,
                        alignment=ft.alignment.center,
                    ),
                    ft.Text(label, size=14, expand=True),
                    ft.Container(
                        content=ft.Text(f"Peso: {value}", size=12, color="#424242"),
                        padding=4,
                        bgcolor="#f3f4f6",
                        border_radius=4,
                    ),
                    ft.Column([
                        ft.IconButton(
                            icon=ft.Icons.KEYBOARD_ARROW_UP,
                            icon_size=20,
                            tooltip="Mover para cima (aumentar prioridade)",
                            on_click=move_up,
                            disabled=(position == 0)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                            icon_size=20,
                            tooltip="Mover para baixo (diminuir prioridade)",
                            on_click=move_down,
                            disabled=(position == len(self.preference_order) - 1)
                        ),
                    ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=12,
                border=ft.border.all(1, "#e5e7eb"),
                border_radius=8,
                bgcolor="white",
            )

    def _on_preferences_click(self, e):
        """Handle preferences button click."""
        try:
            self.append_log("Botão 'Suas Preferências' clicado")
            self._show_preferences(e)
        except Exception as ex:
            self.append_log(f"Erro ao executar _show_preferences: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")

    def _on_ranking_click(self, e):
        """Handle ranking button click."""
        try:
            self.append_log("Botão 'Ranking' clicado")
            self._show_ranking(e)
        except Exception as ex:
            self.append_log(f"Erro ao executar _show_ranking: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")

    def _on_favorites_click(self, e):
        """Handle favorites button click."""
        try:
            self.append_log("Botão 'Favoritos' clicado")
            self._show_favorites(e)
        except Exception as ex:
            self.append_log(f"Erro ao executar _show_favorites: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")


    def _on_search_change(self, e):
        self._apply_filters()

    def _on_sort_change(self, e):
        self._apply_filters()

    def _apply_filters(self):
        search_term = self.search_field.value.lower()
        sort_by = self.sort_dropdown.value or "Nome"

        self.filtered_results = []
        for item in self.results:
            name = str(item.get('Nome do Carro') or item.get('nome') or '').lower()
            marca = str(item.get('Marca') or item.get('marca') or '').lower()
            modelo = str(item.get('Modelo') or item.get('modelo') or '').lower()
            valor = str(item.get('Valor') or item.get('valor') or '').lower()

            if search_term in name or search_term in marca or search_term in modelo or search_term in valor:
                self.filtered_results.append(item)

        self._sort_results(sort_by)
        self.refresh_results_table()

    def _sort_results(self, sort_by: str):
        if sort_by == "Pre��o: Menor para Maior":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('Valor') or x.get('valor') or '0'), reverse=False)
        elif sort_by == "Preço: Maior para Menor":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('Valor') or x.get('valor') or '0'), reverse=True)
        elif sort_by == "KM: Menor para Maior":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('KM') or x.get('Quilometragem') or '999999999'), reverse=False)
        elif sort_by == "KM: Maior para Menor":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('KM') or x.get('Quilometragem') or '0'), reverse=True)
        elif sort_by == "Ano: Mais Novo":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('Ano') or '0'), reverse=True)
        elif sort_by == "Ano: Mais Antigo":
            self.filtered_results.sort(key=lambda x: self._extract_number(x.get('Ano') or '0'), reverse=False)
        elif sort_by == "Curtidos":
            self.filtered_results.sort(key=lambda x: x.get('Link') or x.get('link') or '' in self.liked_items, reverse=True)
        else:
            self.filtered_results.sort(key=lambda x: str(x.get('Nome do Carro') or x.get('nome') or ''))

    def _extract_number(self, value: str) -> float:
        if not value:
            return 0
        import re
        match = re.search(r'\d+\.?\d*', str(value).replace(',', '.'))
        return float(match.group()) if match else 0

    # ========================================================================
    # PREFERENCES METHODS - COMPLETE AND CORRECTED
    # ========================================================================

    def _show_preferences(self, e):
        """Show preferences as reorderable list"""
        try:
            self.append_log("Mostrando preferências como lista reordenável")
            
            labels = {
                'quilometragem': '📏 Quilometragem',
                'potenciaMotor': '⚡ Potência do Motor',
                'portas': '🚪 Número de Portas',
                'ano': '📅 Ano do Veículo',
            }
            
            pref_column = ft.Column([], spacing=8)
            
            def rebuild_preferences():
                pref_column.controls.clear()
                for idx, pref_key in enumerate(self.preference_order):
                    pref_value = self.preferences.get(pref_key, 0)
                    pref_column.controls.append(
                        self._build_preference_item(pref_key, labels.get(pref_key, pref_key), pref_value, idx, rebuild_preferences)
                    )
                # Não precisa de .update() aqui - o diálogo cuida disso
                try:
                    pref_column.update()
                except:
                    pass  # Ignora se não estiver na página ainda

            
            rebuild_preferences()
            
            content = ft.Container(
                content=ft.Column([
                    ft.Text("Arraste ou use as setas para reordenar suas prioridades:", size=12, italic=True, color="#424242"),
                    ft.Divider(),
                    pref_column,
                    ft.Divider(),
                    ft.Text("💡 A ordem define a importância: itens no topo têm mais peso no ranking.", size=11, color="#2563EB"),
                ], spacing=8, scroll=ft.ScrollMode.AUTO),  # ← Adicionar scroll também
                width=500,
                height=500,  # ← MUDADO de 400 para 500
            )
            
            def on_save(e):
                for idx, key in enumerate(self.preference_order):
                    self.preferences[key] = len(self.preference_order) - idx
                self.save_state()
                self.append_log("Preferências salvas e pesos recalculados!")
                # Recalcular melhor match e atualizar resultados
                self.refresh_results_table()
                self._close_dialog()

            
            dlg = ft.AlertDialog(
                title=ft.Text("🎯 Suas Preferências"),
                content=content,
                actions=[
                    ft.TextButton("Salvar", on_click=on_save),
                    ft.TextButton("Fechar", on_click=self._close_dialog),
                ],
                modal=True,
            )
            
            self._current_dialog = dlg
            self._open_alert_dialog(dlg, "Preferências")
            
        except Exception as ex:
            self.append_log(f"Erro ao abrir preferências: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")

    def _build_preference_item(self, key: str, label: str, value: int, position: int, rebuild_callback) -> ft.Container:
        """Build preference list item with reorder controls"""
        
        def move_up(e):
            if position > 0:
                self.preference_order[position], self.preference_order[position-1] = \
                    self.preference_order[position-1], self.preference_order[position]
                rebuild_callback()
        
        def move_down(e):
            if position < len(self.preference_order) - 1:
                self.preference_order[position], self.preference_order[position+1] = \
                    self.preference_order[position+1], self.preference_order[position]
                rebuild_callback()
        
        return ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Text(f"{position + 1}", size=16, weight="bold"),
                    width=30,
                    alignment=ft.alignment.center,
                ),
                
                ft.Text(label, size=14, expand=True),
                
                ft.Container(
                    content=ft.Text(f"Peso: {value}", size=12, color="#424242"),
                    padding=4,
                    bgcolor="#f3f4f6",
                    border_radius=4,
                ),
                
                ft.Column([
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_ARROW_UP,
                        icon_size=20,
                        tooltip="Mover para cima (aumentar prioridade)",
                        on_click=move_up,
                        disabled=(position == 0)
                    ),
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                        icon_size=20,
                        tooltip="Mover para baixo (diminuir prioridade)",
                        on_click=move_down,
                        disabled=(position == len(self.preference_order) - 1)
                    ),
                ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=12,
            border=ft.border.all(1, "#e5e7eb"),
            border_radius=8,
            bgcolor="white",
        )


    def _show_ranking(self, e):
        """Show ranking with full cards and reorder controls"""
        try:
            self.append_log("Abrindo ranking completo com reordenação")
            
            # Criar lista ordenada
            ordered_items = []
            for link in self.ranking_list:
                # Busca em results
                item = next((r for r in self.results if (r.get('Link') or r.get('link')) == link), None)
                # Busca em filtered_results se não encontrou
                if not item:
                    item = next((r for r in self.filtered_results if (r.get('Link') or r.get('link')) == link), None)
                # Busca no cache se não encontrou
                if not item and link in self.liked_items_cache:
                    item = self.liked_items_cache[link]
                    self.append_log(f"✅ Usando cache para: {item.get('Nome', item.get('nome', 'Sem nome'))[:50]}")
                if item:
                    ordered_items.append(item)
                else:
                    self.append_log(f"⚠️ Link não encontrado (nem no cache): {link[:80]}")

            
            if not ordered_items:
                self.append_log(f"Nenhum carro curtido. Liked: {len(self.liked_items)}, Ranking: {len(self.ranking_list)}")
                return
            
            # Criar column para os cards
            ranking_column = ft.Column([], spacing=12, scroll=ft.ScrollMode.AUTO)
            
            def rebuild_ranking():
                ranking_column.controls.clear()
                for idx, item in enumerate(ordered_items, 1):
                    ranking_column.controls.append(
                        self._build_ranking_card_full(item, idx, ordered_items, rebuild_ranking)
                    )
                try:
                    ranking_column.update()
                except:
                    pass
            
            rebuild_ranking()
            
            # ← ADICIONE AQUI A DEFINIÇÃO DO CONTENT
            content = ft.Container(
                content=ranking_column,
                width=900,
                height=600,
            )
            
            dlg = ft.AlertDialog(
                title=ft.Text("🏆 Ranking de Carros Curtidos"),
                content=content,
                actions=[
                    ft.TextButton("Fechar", on_click=lambda e: self._close_dialog())
                ],
                modal=True,
            )
            
            self._current_dialog = dlg
            self._open_alert_dialog(dlg, "Ranking")
            
        except Exception as ex:
            self.append_log(f"Erro ao abrir ranking: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")



            def on_save(e):
                self.ranking_list = [item.get('Link') or item.get('link') for item in ordered_items]
                self.save_state()
                self.append_log("Ranking salvo com sucesso!")
                self._close_dialog()
            dlg = ft.AlertDialog(
                title=ft.Text("🏆 Seu Ranking de Carros"),
                content=content,
                actions=[
                    ft.TextButton("Salvar Ordem", on_click=on_save),
                    ft.TextButton("Fechar", on_click=self._close_dialog)
                ],
                modal=True
            )
            self._current_dialog = dlg
            self._open_alert_dialog(dlg, "Ranking")
        except Exception as ex:
            self.append_log(f"Erro ao abrir ranking: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")


        #
    def _build_ranking_card_full(self, item: Dict[str, Any], position: int, ordered_list: list, rebuild_callback) -> ft.Container:
        """Build full card for ranking with reorder controls and description field"""
        
        # Extrair informações
        image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
        name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
        valor = item.get('Valor') or item.get('valor') or 'N/A'
        link = item.get('Link') or item.get('link') or ''
        portal = item.get('Portal') or item.get('portal') or ''
        ano = item.get('Ano') or 'N/A'
        km = item.get('KM') or item.get('Quilometragem') or 'N/A'
        portas = item.get('Portas') or 'N/A'
        combustivel = item.get('Combustível') or 'N/A'
        motor = item.get('Motor') or item.get('Potência do Motor') or 'N/A'
        
        # Botão para abrir modal de descrição
        current_description = self.ranking_descriptions.get(link, '')
        description_preview = current_description[:50] + "..." if len(current_description) > 50 else current_description or "Adicionar justificativa"
        
        # Criar closure com valores corretos
        car_name = name
        car_link = link
        car_current_desc = current_description
        
        def open_description_modal(e):
            description_text_field = ft.TextField(
                label="Por que esse carro está nessa posição?",
                value=car_current_desc,
                multiline=True,
                min_lines=5,
                max_lines=10,
                width=500,
                expand=True,
            )
            
            def save_description(e):
                self._update_ranking_description(car_link, description_text_field.value)
                self._close_dialog()
                self._show_ranking(None)
            
            def cancel_description(e):
                self._close_dialog()
            
            description_dlg = ft.AlertDialog(
                title=ft.Text(f"✏️ Justificativa - {car_name}"),
                content=ft.Container(
                    content=ft.Column([
                        ft.Text("Explique por que esse carro está nessa posição do seu ranking:", size=12, color="#424242"),
                        description_text_field,
                    ], spacing=8),
                    width=500,
                    height=300,
                ),
                actions=[
                    ft.TextButton("Cancelar", on_click=cancel_description),
                    ft.ElevatedButton("Salvar", on_click=save_description, bgcolor="#2563EB", color="white"),
                ],
                modal=True,
            )
            
            self._current_dialog = description_dlg
            self._open_alert_dialog(description_dlg, "Descrição do Ranking")
        
        description_button = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.EDIT_NOTE, size=20, color="#2563EB"),
                ft.Text(description_preview, size=12, color="#2563EB", italic=True if not current_description else False),
            ], spacing=8),
            padding=8,
            border=ft.border.all(1, "#2563EB"),
            border_radius=8,
            bgcolor="#eff6ff",
            on_click=open_description_modal,
            tooltip="Clique para editar a justificativa",
        )
        
        # Funções de reordenação
        def move_up(e):
            idx = ordered_list.index(item)
            if idx > 0:
                ordered_list[idx], ordered_list[idx-1] = ordered_list[idx-1], ordered_list[idx]
                rebuild_callback()
        
        def move_down(e):
            idx = ordered_list.index(item)
            if idx < len(ordered_list) - 1:
                ordered_list[idx], ordered_list[idx+1] = ordered_list[idx+1], ordered_list[idx]
                rebuild_callback()
        
        def openlink():
            if link:
                try:
                    self.page.launch_url(link)
                except Exception as ex:
                    self.append_log(f"Erro ao abrir link: {ex}")
        
        # Card completo
        card_content = ft.Container(
            content=ft.Column([
                # Cabeçalho com posição e controles
                ft.Row([
                    ft.Container(
                        content=ft.Text(f"#{position}", size=20, weight="bold", color="#2563EB"),
                        bgcolor="#eff6ff",
                        padding=8,
                        border_radius=8,
                        width=50,
                        alignment=ft.alignment.center
                    ),
                    ft.Column([
                        ft.IconButton(
                            icon=ft.Icons.ARROW_UPWARD,
                            icon_size=16,
                            tooltip="Mover para cima",
                            on_click=move_up,
                            disabled=(position == 1)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.ARROW_DOWNWARD,
                            icon_size=16,
                            tooltip="Mover para baixo",
                            on_click=move_down,
                            disabled=(position == len(ordered_list))
                        ),
                    ], spacing=0),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                
                ft.Divider(height=1),
                
                # Conteúdo principal
                ft.Row([
                    ft.Container(
                        content=ft.Image(src=image_src, width=200, height=150, fit=ft.ImageFit.COVER),
                        border_radius=8,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                    
                    ft.Column([
                        ft.Text(name, size=16, weight="bold"),
                        ft.Text(f"💰 {valor}", size=14, color="#16a34a", weight="bold"),
                        ft.Text(f"📍 {portal}", size=13, color="#1565c0", weight="bold"),
                        ft.Divider(height=1),
                        ft.Row([
                            ft.Column([
                                ft.Text("Ano:", size=10, color="#424242"),
                                ft.Text(str(ano), size=12, weight="bold"),
                            ], spacing=2),
                            ft.Column([
                                ft.Text("KM:", size=10, color="#424242"),
                                ft.Text(str(km), size=12, weight="bold"),
                            ], spacing=2),
                            ft.Column([
                                ft.Text("Portas:", size=10, color="#424242"),
                                ft.Text(str(portas), size=12, weight="bold"),
                            ], spacing=2),
                            ft.Column([
                                ft.Text("Combustível:", size=10, color="#424242"),
                                ft.Text(str(combustivel), size=12, weight="bold"),
                            ], spacing=2),
                        ], spacing=12),
                        ft.Text(f"⚡ Motor: {motor}", size=11, color="#2563EB"),
                    ], spacing=4, expand=True),
                ], spacing=12),
                
                # Campo de descrição
                description_button,
                
                # Botão de ação
                ft.Row([
                    ft.ElevatedButton(
                        "🔗 Abrir Anúncio",
                        on_click=lambda e: openlink(),
                        bgcolor="#2563EB",
                        color="white"
                    ),
                ], alignment=ft.MainAxisAlignment.END),
                
            ], spacing=8),
            padding=16,
            border=ft.border.all(2, "#2563EB" if position <= 3 else "#e5e7eb"),
            border_radius=12,
            bgcolor="#fafafa",
        )
        
        return card_content

    def _update_ranking_description(self, link: str, description: str):
        """Update ranking description for a car"""
        self.ranking_descriptions[link] = description
        self.save_state()
##
##        def _build_ranking_card_full(self, item: Dict[str, Any], position: int, ordered_list: list, rebuild_callback) -> ft.Container:
##            # Extrai informações do carro
##            image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
##            name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
##            valor = item.get('Valor') or item.get('valor') or 'N/A'
##            link = item.get('Link') or item.get('link') or ''
##            portal = item.get('Portal') or item.get('portal') or ''
##            ano = item.get('Ano') or 'N/A'
##            km = item.get('KM') or item.get('Quilometragem') or 'N/A'
##            portas = item.get('Portas') or 'N/A'
##            combustivel = item.get('Combustível') or 'N/A'
##            motor = item.get('Motor') or item.get('Potência do Motor') or 'N/A'
##            current_description = self.ranking_descriptions.get(link, '')
##            description_field = ft.TextField(
##                label="Por que está nessa posição?",
##                value=current_description,
##                multiline=True,
##                min_lines=2,
##                max_lines=4,
##                width=600,
##                on_change=lambda e: self._update_ranking_description(link, e.control.value)
##            )
##            def move_up(e):
##                idx = ordered_list.index(item)
##                if idx > 0:
##                    ordered_list[idx], ordered_list[idx-1] = ordered_list[idx-1], ordered_list[idx]
##                    rebuild_callback()
##            def move_down(e):
##                idx = ordered_list.index(item)
##                if idx < len(ordered_list) - 1:
##                    ordered_list[idx], ordered_list[idx+1] = ordered_list[idx+1], ordered_list[idx]
##                    rebuild_callback()
##            def openlink():
##                if link:
##                    try:
##                        self.page.launch_url(link)
##                    except Exception as ex:
##                        self.append_log(f"Erro ao abrir link: {ex}")
##            card_content = ft.Container(
##                content=ft.Column([
##                    ft.Row([
##                        ft.Container(
##                            content=ft.Text(f"#{position}", size=20, weight="bold", color="#2563EB"),
##                            bgcolor="#eff6ff",
##                            padding=8,
##                            border_radius=8,
##                            width=50,
##                            alignment=ft.alignment.center
##                        ),
##                        ft.Column([
##                            ft.IconButton(
##                                icon=ft.Icons.ARROW_UPWARD,
##                                icon_size=16,
##                                tooltip="Mover para cima",
##                                on_click=move_up,
##                                disabled=(position == 1)
##                            ),
##                            ft.IconButton(
##                                icon=ft.Icons.ARROW_DOWNWARD,
##                                icon_size=16,
##                                tooltip="Mover para baixo",
##                                on_click=move_down,
##                                disabled=(position == len(ordered_list))
##                            ),
##                        ], spacing=0),
##                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
##                    ft.Divider(height=1),
##                    ft.Row([
##                        ft.Container(
##                            content=ft.Image(src=image_src, width=200, height=150, fit=ft.ImageFit.COVER),
##                            border_radius=8,
##                            clip_behavior=ft.ClipBehavior.HARD_EDGE,
##                        ),
##                        ft.Column([
##                            ft.Text(name, size=16, weight="bold"),
##                            ft.Text(f"💰 {valor}", size=14, color="#16a34a", weight="bold"),
##                            ft.Text(f"📍 {portal}", size=13, color="#1565c0", weight="bold"),
##                            ft.Divider(height=1),
##                            ft.Row([
##                                ft.Column([ft.Text("Ano:", size=10, color="#424242"), ft.Text(str(ano), size=12, weight="bold")], spacing=2),
##                                ft.Column([ft.Text("KM:", size=10, color="#424242"), ft.Text(str(km), size=12, weight="bold")], spacing=2),
##                                ft.Column([ft.Text("Portas:", size=10, color="#424242"), ft.Text(str(portas), size=12, weight="bold")], spacing=2),
##                                ft.Column([ft.Text("Combustível:", size=10, color="#424242"), ft.Text(str(combustivel), size=12, weight="bold")], spacing=2),
##                            ], spacing=12),
##                            ft.Text(f"⚡ Motor: {motor}", size=11, color="#2563EB")
##                        ], spacing=4, expand=True),
##                    ], spacing=12),
##                    description_field,
##                    ft.Row([
##                        ft.ElevatedButton(
##                            "🔗 Abrir Anúncio",
##                            on_click=lambda e: openlink(),
##                            bgcolor="#2563EB",
##                            color="white"
##                        ),
##                    ], alignment=ft.MainAxisAlignment.END),
##                ], spacing=8),
##                padding=16,
##                border=ft.border.all(2, "#2563EB" if position <= 3 else "#e5e7eb"),
##                border_radius=12,
##                bgcolor="#fafafa",
##            )
##            return card_content



##    def _show_ranking(self, e):
##        """Show ranking with full cards and reordering capability"""
##        try:
##            self.append_log("Abrindo ranking completo com reordenação")
##            
##            liked_items = [item for item in self.results if (item.get('Link') or item.get('link') or '') in self.liked_items]
##            
##            if not liked_items:
##                dlg = ft.AlertDialog(
##                    title=ft.Text("🏆 Ranking"),
##                    content=ft.Text("Nenhum carro curtido encontrado. Curta alguns carros primeiro!"),
##                    actions=[ft.TextButton("Fechar", on_click=self._close_dialog)],
##                    modal=True
##                )
##                self._current_dialog = dlg
##                self._open_alert_dialog(dlg, "Ranking")
##                return
##            
##            # Ordenar de acordo com ranking_list
##            ordered_items = []
##            for link in self.ranking_list:
##                for item in liked_items:
##                    if (item.get('Link') or item.get('link')) == link:
##                        ordered_items.append(item)
##                        break
##            
##            # Adicionar itens curtidos que não estão no ranking ainda
##            for item in liked_items:
##                link = item.get('Link') or item.get('link')
##                if link not in self.ranking_list:
##                    ordered_items.append(item)
##                    self.ranking_list.append(link)
##            
##            # Criar coluna scrollável com cards
##            ranking_column = ft.Column([], spacing=12, scroll=ft.ScrollMode.AUTO)

##    def _build_ranking_card_full(self, item: Dict[str, Any], position: int, ordered_list: list, rebuild_callback) -> ft.Container:
##        """Build full card for ranking with reorder controls and description field"""
##        
##        image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
##        name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
##        valor = item.get('Valor') or item.get('valor') or 'N/A'
##        link = item.get('Link') or item.get('link') or ''
##        portal = item.get('Portal') or item.get('portal') or ''
##        ano = item.get('Ano') or 'N/A'
##        km = item.get('KM') or item.get('Quilometragem') or 'N/A'
##        portas = item.get('Portas') or 'N/A'
##        combustivel = item.get('Combustível') or 'N/A'
##        motor = item.get('Motor') or item.get('Potência do Motor') or 'N/A'
##        
##        current_description = self.ranking_descriptions.get(link, '')
##        description_field = ft.TextField(
##            label="Por que está nessa posição?",
##            value=current_description,
##            multiline=True,
##            min_lines=2,
##            max_lines=4,
##            width=600,
##            on_change=lambda e: self._update_ranking_description(link, e.control.value)
##        )
##        
##        def move_up(e):
##            idx = ordered_list.index(item)
##            if idx > 0:
##                ordered_list[idx], ordered_list[idx-1] = ordered_list[idx-1], ordered_list[idx]
##                rebuild_callback()
##        
##        def move_down(e):
##            idx = ordered_list.index(item)
##            if idx < len(ordered_list) - 1:
##                ordered_list[idx], ordered_list[idx+1] = ordered_list[idx+1], ordered_list[idx]
##                rebuild_callback()
##        
##        def openlink():
##            if link:
##                try:
##                    self.page.launch_url(link)
##                except Exception as ex:
##                    self.append_log(f"Erro ao abrir link: {ex}")
##        
##        card_content = ft.Container(
##            content=ft.Column([
##                ft.Row([
##                    ft.Container(
##                        content=ft.Text(f"#{position}", size=20, weight="bold", color="#2563EB"),
##                        bgcolor="#eff6ff",
##                        padding=8,
##                        border_radius=8,
##                        width=50,
##                        alignment=ft.alignment.center
##                    ),
##                    ft.Column([
##                        ft.IconButton(
##                            icon=ft.Icons.ARROW_UPWARD,
##                            icon_size=16,
##                            tooltip="Mover para cima",
##                            on_click=move_up,
##                            disabled=(position == 1)
##                        ),
##                        ft.IconButton(
##                            icon=ft.Icons.ARROW_DOWNWARD,
##                            icon_size=16,
##                            tooltip="Mover para baixo",
##                            on_click=move_down,
##                            disabled=(position == len(ordered_list))
##                        ),
##                    ], spacing=0),
##                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
##                
##                ft.Divider(height=1),
##                
##                ft.Row([
##                    ft.Container(
##                        content=ft.Image(src=image_src, width=200, height=150, fit=ft.ImageFit.COVER),
##                        border_radius=8,
##                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
##                    ),
##                    
##                    ft.Column([
##                        ft.Text(name, size=16, weight="bold"),
##                        ft.Text(f"💰 {valor}", size=14, color="#16a34a", weight="bold"),
##                        ft.Text(f"📍 {portal}", size=13, color="#1565c0", weight="bold"),
##                        ft.Divider(height=1),
##                        ft.Row([
##                            ft.Column([
##                                ft.Text("Ano:", size=10, color="#424242"),
##                                ft.Text(str(ano), size=12, weight="bold"),
##                            ], spacing=2),
##                            ft.Column([
##                                ft.Text("KM:", size=10, color="#424242"),
##                                ft.Text(str(km), size=12, weight="bold"),
##                            ], spacing=2),
##                            ft.Column([
##                                ft.Text("Portas:", size=10, color="#424242"),
##                                ft.Text(str(portas), size=12, weight="bold"),
##                            ], spacing=2),
##                            ft.Column([
##                                ft.Text("Combustível:", size=10, color="#424242"),
##                                ft.Text(str(combustivel), size=12, weight="bold"),
##                            ], spacing=2),
##                        ], spacing=12),
##                        ft.Text(f"⚡ Motor: {motor}", size=11, color="#2563EB"),
##                    ], spacing=4, expand=True),
##                ], spacing=12),
##                
##                description_field,
##                
##                ft.Row([
##                    ft.ElevatedButton(
##                        "🔗 Abrir Anúncio",
##                        on_click=lambda e: openlink(),
##                        bgcolor="#2563EB",
##                        color="white"
##                    ),
##                ], alignment=ft.MainAxisAlignment.END),
##                
##            ], spacing=8),
##            padding=16,
##            border=ft.border.all(2, "#2563EB" if position <= 3 else "#e5e7eb"),
##            border_radius=12,
##            bgcolor="#fafafa",
##        )
##        
##        return card_content
##
##
##    def _update_ranking_description(self, link: str, description: str):
##        """Update ranking description for a car"""
##        self.ranking_descriptions[link] = description
##        self.save_state()
##
##    def move_up(e):
##        idx = ordered_list.index(item)
##        if idx > 0:
##            ordered_list[idx], ordered_list[idx-1] = ordered_list[idx-1], ordered_list[idx]
##            rebuild_callback()
##    
##    def move_down(e):
##        idx = ordered_list.index(item)
##        if idx < len(ordered_list) - 1:
##            ordered_list[idx], ordered_list[idx+1] = ordered_list[idx+1], ordered_list[idx]
##            rebuild_callback()
##    
##    def openlink():
##        if link:
##            try:
##                self.page.launch_url(link)
##            except Exception as ex:
##                self.append_log(f"Erro ao abrir link: {ex}")
##    
##    card_content = ft.Container(
##        content=ft.Column([
##            ft.Row([
##                ft.Container(
##                    content=ft.Text(f"#{position}", size=20, weight="bold", color="#2563EB"),
##                    bgcolor="#eff6ff",
##                    padding=8,
##                    border_radius=8,
##                    width=50,
##                    alignment=ft.alignment.center
##                ),
##                ft.Column([
##                    ft.IconButton(
##                        icon=ft.Icons.ARROW_UPWARD,
##                        icon_size=16,
##                        tooltip="Mover para cima",
##                        on_click=move_up,
##                        disabled=(position == 1)
##                    ),
##                    ft.IconButton(
##                        icon=ft.Icons.ARROW_DOWNWARD,
##                        icon_size=16,
##                        tooltip="Mover para baixo",
##                        on_click=move_down,
##                        disabled=(position == len(ordered_list))
##                    ),
##                ], spacing=0),
##            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
##            
##            ft.Divider(height=1),
##            
##            ft.Row([
##                ft.Container(
##                    content=ft.Image(src=image_src, width=200, height=150, fit=ft.ImageFit.COVER),
##                    border_radius=8,
##                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
##                ),
##                
##                ft.Column([
##                    ft.Text(name, size=16, weight="bold"),
##                    ft.Text(f"💰 {valor}", size=14, color="#16a34a", weight="bold"),
##                    ft.Text(f"📍 {portal}", size=13, color="#1565c0", weight="bold"),
##                    ft.Divider(height=1),
##                    ft.Row([
##                        ft.Column([
##                            ft.Text("Ano:", size=10, color="#424242"),
##                            ft.Text(str(ano), size=12, weight="bold"),
##                        ], spacing=2),
##                        ft.Column([
##                            ft.Text("KM:", size=10, color="#424242"),
##                            ft.Text(str(km), size=12, weight="bold"),
##                        ], spacing=2),
##                        ft.Column([
##                            ft.Text("Portas:", size=10, color="#424242"),
##                            ft.Text(str(portas), size=12, weight="bold"),
##                        ], spacing=2),
##                        ft.Column([
##                            ft.Text("Combustível:", size=10, color="#424242"),
##                            ft.Text(str(combustivel), size=12, weight="bold"),
##                        ], spacing=2),
##                    ], spacing=12),
##                    ft.Text(f"⚡ Motor: {motor}", size=11, color="#2563EB"),
##                ], spacing=4, expand=True),
##            ], spacing=12),
##            
##            description_field,
##            
##            ft.Row([
##                ft.ElevatedButton(
##                    "🔗 Abrir Anúncio",
##                    on_click=lambda e: openlink(),
##                    bgcolor="#2563EB",
##                    color="white"
##                ),
##            ], alignment=ft.MainAxisAlignment.END),
##            
##        ], spacing=8),
##        padding=16,
##        border=ft.border.all(2, "#2563EB" if position <= 3 else "#e5e7eb"),
##        border_radius=12,
##        bgcolor="#fafafa",
##    )
##    
##    return card_content

##def _update_ranking_description(self, link: str, description: str):
##    """Update ranking description for a car"""
##    self.ranking_descriptions[link] = description
##    self.save_state()
##
##            
##            def rebuild_ranking():
##                ranking_column.controls.clear()
##                for idx, item in enumerate(ordered_items, 1):
##                    ranking_column.controls.append(
##                        self._build_ranking_card_full(item, idx, ordered_items, rebuild_ranking)
##                    )
##                ranking_column.update()
##            
##            rebuild_ranking()
##            
##            content = ft.Container(
##                content=ranking_column,
##                width=800,
##                height=600,
##            )
##            
##            def on_save(e):
##                self.ranking_list = [item.get('Link') or item.get('link') for item in ordered_items]
##                self.save_state()
##                self.append_log("Ranking salvo com sucesso!")
##                self._close_dialog()
##            
##            dlg = ft.AlertDialog(
##                title=ft.Text("🏆 Seu Ranking de Carros"),
##                content=content,
##                actions=[
##                    ft.TextButton("Salvar Ordem", on_click=on_save),
##                    ft.TextButton("Fechar", on_click=self._close_dialog)
##                ],
##                modal=True
##            )
##            
##            self._current_dialog = dlg
##            self._open_alert_dialog(dlg, "Ranking")
##            
##        except Exception as ex:
##            self.append_log(f"Erro ao abrir ranking: {ex}")
##            import traceback
##            self.append_log(f"Stack trace: {traceback.format_exc()}")
##
##
##
##
##
##    def _close_ranking_dialog(self):
##        if hasattr(self, '_ranking_dialog'):
##            self._ranking_dialog.open = False
##            self.page.update()

  

    def _build_favorite_card_full(self, item: Dict[str, Any]) -> ft.Container:
        image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
        name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
        valor = item.get('Valor') or item.get('valor') or 'N/A'
        link = item.get('Link') or item.get('link') or ''
        portal = item.get('Portal') or item.get('portal') or ''
        ano = item.get('Ano') or 'N/A'
        km = item.get('KM') or item.get('Quilometragem') or 'N/A'
        portas = item.get('Portas') or 'N/A'
        combustivel = item.get('Combustível') or 'N/A'
        motor = item.get('Motor') or item.get('Potência do Motor') or 'N/A'
        cor = item.get('Cor') or 'N/A'
        cambio = item.get('Câmbio') or item.get('Transmissão') or 'N/A'
        
        def openlink():
            if link:
                try:
                    self.page.launch_url(link)
                except Exception as ex:
                    self.append_log(f"Erro ao abrir link: {ex}")
        def remove_favorite(e):
            if link in self.liked_items:
                self.liked_items.remove(link)
                self.save_state()
                self.refresh_results_table()
                self._close_dialog()
                self._show_favorites(None)
        card_content = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Container(
                        content=ft.Image(src=image_src, width=250, height=180, fit=ft.ImageFit.COVER),
                        border_radius=12,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                    ft.Column([
                        ft.Text(name, size=18, weight="bold"),
                        ft.Text(f"💰 {valor}", size=16, color="#16a34a", weight="bold"),
                        ft.Text(f"📍 {portal}", size=13, color="#1565c0", weight="bold"),
                        ft.Divider(height=1),
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Column([ft.Text("Ano", size=10, color="#424242"), ft.Text(str(ano), size=13, weight="bold")], spacing=2),
                                    ft.Column([ft.Text("KM", size=10, color="#424242"), ft.Text(str(km), size=13, weight="bold")], spacing=2),
                                    ft.Column([ft.Text("Portas", size=10, color="#424242"), ft.Text(str(portas), size=13, weight="bold")], spacing=2),
                                ], spacing=16),
                                ft.Row([
                                    ft.Column([ft.Text("Combustível", size=10, color="#424242"), ft.Text(str(combustivel), size=13, weight="bold")], spacing=2),
                                    ft.Column([ft.Text("Câmbio", size=10, color="#424242"), ft.Text(str(cambio), size=13, weight="bold")], spacing=2),
                                    ft.Column([ft.Text("Cor", size=10, color="#424242"), ft.Text(str(cor), size=13, weight="bold")], spacing=2),
                                ], spacing=16),
                            ], spacing=8),
                            padding=8,
                            bgcolor="#f9fafb",
                            border_radius=8,
                        ),
                        ft.Text(f"⚡ Motor: {motor}", size=12, color="#2563EB", weight="bold"),
                    ], spacing=6, expand=True),
                ], spacing=16),
                ft.Row([
                    ft.ElevatedButton(
                        "🔗 Abrir Anúncio",
                        on_click=lambda e: openlink(),
                        bgcolor="#2563EB",
                        color="white",
                    ),
                    ft.OutlinedButton(
                        "❌ Remover dos Favoritos",
                        on_click=remove_favorite,
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=12),
            padding=16,
            border=ft.border.all(2, "#fbbf24"),
            border_radius=12,
            bgcolor="#fffbeb",
        )
        return card_content


    def _show_favorites(self, e):
        """Show favorites with full cards"""
        try:
            self.append_log("Mostrando favoritos com cards completos")
            favs = [item for item in self.results if (item.get('Link') or item.get('link') or '') in self.liked_items]
            if not favs:
                dlg = ft.AlertDialog(
                    title=ft.Text("⭐ Favoritos"),
                    content=ft.Text("Nenhum favorito encontrado. Curta alguns carros primeiro!"),
                    actions=[ft.TextButton("Fechar", on_click=self._close_dialog)],
                    modal=True
                )
                self._current_dialog = dlg
                self._open_alert_dialog(dlg, "Favoritos")
                return
            controls = []
            for item in favs:
                controls.append(self._build_favorite_card_full(item))
            content = ft.Container(
                content=ft.Column(controls, spacing=16, scroll=ft.ScrollMode.AUTO),
                width=800,
                height=600,
            )
            dlg = ft.AlertDialog(
                title=ft.Text(f"⭐ Seus Favoritos ({len(favs)} carros)"),
                content=content,
                actions=[ft.TextButton("Fechar", on_click=self._close_dialog)],
                modal=True
            )
            self._current_dialog = dlg
            self._open_alert_dialog(dlg, "Favoritos")
        except Exception as ex:
            self.append_log(f"Erro ao abrir favoritos: {ex}")
            import traceback
            self.append_log(f"Stack trace: {traceback.format_exc()}")

    def _close_favorites_dialog(self):
        try:
            if hasattr(self, '_favorites_dialog') and self._favorites_dialog:
                self._favorites_dialog.open = False
                self.page.update()
        except Exception:
            pass

    def _calculate_best_match(self) -> Optional[str]:
        """Calculate the best matching car based on preferences"""
        if not self.filtered_results:
            return None

        def calculate_score(item):
            score = 0
            total_weight = sum(self.preferences.values())
            if total_weight == 0:
                return 0

            peso_km = self.preferences.get('quilometragem', 0)
            peso_potencia = self.preferences.get('potenciaMotor', 0)
            peso_portas = self.preferences.get('portas', 0)
            peso_ano = self.preferences.get('ano', 0)

            # KM (lower is better - inverse score)
            try:
                km_str = str(item.get('KM') or item.get('Quilometragem') or '999999').replace('.', '').replace(',', '')
                km = int(''.join(filter(str.isdigit, km_str)) or '0')
                km_score = max(0, 10 - (km / 20000)) * peso_km
                score += km_score
            except:
                pass

            # Potência (higher is better)
            try:
                potencia_str = str(item.get('Motor') or item.get('Potência do Motor') or item.get('Potência') or '0')
                potencia = int(''.join(filter(str.isdigit, potencia_str)) or '0')
                potencia_score = (potencia / 500) * 10 * peso_potencia if potencia > 0 else 0
                score += potencia_score
            except:
                pass

            # Portas (higher is better)
            try:
                portas_str = str(item.get('Portas') or '0')
                portas = int(''.join(filter(str.isdigit, portas_str)) or '0')
                portas_score = (portas / 5) * 10 * peso_portas
                score += portas_score
            except:
                pass

            # Ano (newer is better)
            try:
                ano = int(item.get('Ano') or '2000')
                ano_score = ((ano - 2000) / 25) * 10 * peso_ano
                score += ano_score
            except:
                pass

            return score

        best_item = None
        best_score = -1
        best_link = None

        for item in self.filtered_results:
            link = item.get('Link') or item.get('link') or ''
            if link in self.hidden_items:
                continue
            score = calculate_score(item)
            if score > best_score:
                best_score = score
                best_item = item
                best_link = link

        return best_link

    def _calculate_ranking(self) -> List[Dict[str, Any]]:
        ranked = []
        total_weight = sum(self.preferences.values())

        for item in self.filtered_results:
            score = 0

            km_val = self._extract_number(item.get('KM') or item.get('Quilometragem') or '999999999')
            km_weight = self.preferences.get('quilometragem', 4) / total_weight if total_weight > 0 else 0
            score += (1 - min(km_val / 500000, 1)) * 100 * km_weight

            potencia_val = self._extract_number(item.get('Potência do Motor') or item.get('potencia_motor') or '0')
            potencia_weight = self.preferences.get('potenciaMotor', 3) / total_weight if total_weight > 0 else 0
            score += (potencia_val / 500) * 100 * potencia_weight

            portas_val = self._extract_number(item.get('Portas') or item.get('portas') or '0')
            portas_weight = self.preferences.get('portas', 2) / total_weight if total_weight > 0 else 0
            score += (portas_val / 5) * 100 * portas_weight

            ano_val = self._extract_number(item.get('Ano') or item.get('ano') or '2000')
            ano_weight = self.preferences.get('ano', 1) / total_weight if total_weight > 0 else 0
            score += (ano_val - 2000) / 25 * 100 * ano_weight

            link = item.get('Link') or item.get('link') or ''
            if link in self.liked_items:
                score += 50

            item_copy = item.copy()
            item_copy['_ranking_score'] = score
            ranked.append(item_copy)

        ranked.sort(key=lambda x: x.get('_ranking_score', 0), reverse=True)
        return ranked

    def _build_ranking_card(self, item: Dict[str, Any], position: int) -> ft.Card:
        name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
        valor = item.get('Valor') or item.get('valor') or ''
        image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
        link = item.get('Link') or item.get('link') or ''

        def openlink():
            if link:
                try:
                    self.page.launch_url(link)
                except Exception as ex:
                    self.append_log(f"Erro ao abrir anúncio: {ex}")

        img = ft.Container(
            content=ft.Image(src=image_src, width=80, height=56, fit=ft.ImageFit.COVER),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border_radius=4,
        )

        return ft.Card(ft.Container(ft.Row([
            ft.Text(f"{position}", size=16, weight="bold", width=30),
            img,
            ft.Column([
                ft.Text(name, size=12, weight="bold"),
                ft.Text(valor, size=11),
            ], expand=True),
            ft.Row([
                ft.ElevatedButton("Abrir", on_click=lambda e: openlink(), height=32),
            ]),
        ], spacing=8, expand=True), padding=8), expand=False)

    def on_start(self, e):
        if self.child and self.child.poll() is None:
            self.append_log("Scraper já em execução")
            return

        self.show_loading_screen()

        filters = {
            "cidade": self.cidade.value,
            "anoMin": self.ano_min.value,
            "anoMax": self.ano_max.value,
            "precoMin": self.preco_min.value,
            "precoMax": self.preco_max.value,
            "kmMin": self.km_min.value,
            "kmMax": self.km_max.value,
            "marca": self.marca.value,
            "modelo": self.modelo.value,
            "carroceria": self.carroceria.value,
            "combustivel": self.combustivel.value,
            "portas": self.portas.value,
            "transmissao": self.transmissao.value,
            "cor": self.cor.value,
            "motor": self.motor.value,
            "direcao": self.direcao.value,
            "tracao": self.tracao.value,
            "aceita_troca": self.aceita_troca.value,
            "blindado": self.blindado.value,
            "versao": self.versao.value,
            "tipo_veiculo": self.tipo_veiculo.value,
            "situacao": self.situacao.value,
            "portals": [],
            "captureDetails": self.capture_details.value,
            "capture_details": self.capture_details.value,
            "forbiddenWords": [w.strip() for w in (self.forbidden.value or "").split(",") if w.strip()],
            "forbidden_words": [w.strip() for w in (self.forbidden.value or "").split(",") if w.strip()],
            "zenrowsApiKey": self.zenrows_key.value or None,
            "zenrows_api_key": self.zenrows_key.value or None,
            "km_min": self.km_min.value,
            "km_max": self.km_max.value,
            "scraping_speed": self.scraping_speed,
        }
        if self.portal_olx.value:
            filters["portals"].append("OLX")
        if self.portal_webmotors.value:
            filters["portals"].append("Webmotors")
        if self.portal_ml.value:
            filters["portals"].append("Mercado Livre")
        if self.portal_seminovos.value:
            filters["portals"].append("Seminovos")
        if self.portal_localiza.value:
            filters["portals"].append("Localiza")
        if self.portal_unidas.value:
            filters["portals"].append("Unidas")

        filters_json = json.dumps(filters, ensure_ascii=False)
        self.append_log("Iniciando scraper com filtros: " + json.dumps(filters, ensure_ascii=False))
        remove_stop_signal()

        try:
            cmd = [sys.executable, __file__, filters_json]
            self.child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            self.stop_btn.disabled = False
            self.start_btn.disabled = True
            self.page.update()
            threading.Thread(target=self._read_output_thread, daemon=True).start()
            threading.Thread(target=self._read_error_thread, daemon=True).start()
        except Exception as ex:
            self.append_log(f"Erro ao iniciar processo Python: {ex}")

    def _read_output_thread(self):
        assert self.child and self.child.stdout
        for raw in self.child.stdout:
            line = raw.rstrip("\n")
            self.append_log(line)
            self.add_loading_log(line)
            if line.startswith("EVENT_JSON:"):
                payload = line[len("EVENT_JSON:"):]
                try:
                    item = json.loads(payload)
                    self.add_result(item)
                except Exception:
                    pass
            elif line.startswith("RESULTADO_JSON:"):
                payload = line[len("RESULTADO_JSON:"):]
                try:
                    data = json.loads(payload)
                    self.results = data
                    self.filtered_results = data.copy()
                    self.refresh_results_table()
                    self.append_log(f"Scraping finalizado com {len(data)} items")
                    self.add_loading_log(f"Scraping finalizado com {len(data)} items")
                    self.export_btn.disabled = False if len(data) > 0 else True
                    self.hide_loading_screen()
                    self.page.update()
                except Exception as e:
                    self.append_log(f"Erro ao parsear RESULTADO_JSON: {e}")
            elif line.startswith("EVENT_EXCEL_SAVED:"):
                fname = line[len("EVENT_EXCEL_SAVED:"):]
                self.append_log(f"Excel salvo pelo scraper: {fname}")
        self.append_log("Processo Python finalizado")
        self.add_loading_log("Processo finalizado")
        self.stop_btn.disabled = True
        self.start_btn.disabled = False
        self.hide_loading_screen()
        try:
            if self.child:
                self.child.stdout.close()
        except Exception:
            pass
        self.page.update()

    def _read_error_thread(self):
        assert self.child and self.child.stderr
        for raw in self.child.stderr:
            line = raw.rstrip("\n")
            self.append_log("ERR: " + line)

    def add_result(self, item: Dict[str, Any]):
        self.results.append(item)
        self.filtered_results = self.results.copy()
        self.update_loading_stats(len(self.results))
        card = self._build_card(item)
        self.results_view.controls.append(card)
        self.page.update()
        self.export_btn.disabled = False

        if len(self.results) % 5 == 0:
            self.save_state()

    def refresh_results_table(self):
        try:
            self.append_log(f"Atualizando resultados: total={len(self.filtered_results)}")
            self.best_match_link = self._calculate_best_match()
            self.append_log(f"Melhor match calculado: {self.best_match_link}")
            self.results_view.controls.clear()
            for idx, item in enumerate(self.filtered_results):
                link = item.get('Link') or item.get('link') or ''
                if link not in self.hidden_items:
                    self.append_log(f"Adicionando item #{idx} ao results_view: link={link}")
                    self.results_view.controls.append(self._build_card(item))
            self.results_view.update()
            self.append_log("Refresh da lista de resultados concluído")
        except Exception as e:
            self.append_log(f"Erro ao atualizar resultados: {e}")

    def _toggle_like(self, item: Dict[str, Any]):
        link = item.get('Link') or item.get('link') or ''
        if link:
            if link in self.liked_items:
                # Removendo
                self.liked_items.remove(link)
                if link in self.ranking_list:
                    self.ranking_list.remove(link)
                if link in self.ranking_descriptions:
                    del self.ranking_descriptions[link]
                if link in self.liked_items_cache:
                    del self.liked_items_cache[link]
            else:
                # Adicionando
                self.liked_items.add(link)
                # Salvar dados completos do item
                self.liked_items_cache[link] = dict(item)
                # Adicionar ao ranking se não estiver
                if link not in self.ranking_list:
                    self.ranking_list.append(link)
            self.save_state()
            self.refresh_results_table()

    def _toggle_hide(self, item: Dict[str, Any]):
        link = item.get('Link') or item.get('link') or ''
        if link:
            if link in self.hidden_items:
                self.hidden_items.remove(link)
            else:
                self.hidden_items.add(link)
            self.save_state()
            self.refresh_results_table()

    def _remove_item(self, item: Dict[str, Any]):
        link = item.get('Link') or item.get('link') or ''
        if link:
            self.results = [r for r in self.results if (r.get('Link') or r.get('link') or '') != link]
            self.filtered_results = [r for r in self.filtered_results if (r.get('Link') or r.get('link') or '') != link]
            if link in self.liked_items:
                self.liked_items.remove(link)
            if link in self.hidden_items:
                self.hidden_items.remove(link)
            if link in self.ranking_list:
                self.ranking_list.remove(link)
            if link in self.ranking_descriptions:
                del self.ranking_descriptions[link]
            self.append_log(f"Item removido permanentemente")
            self.save_state()
            self.refresh_results_table()

    def _show_image_preview(self, image_src: str, title: str = ""):
        """Show image in a maximized preview modal"""
        def close_modal(e):
            dlg.open = False
            self.page.update()

        dlg = ft.AlertDialog(
            title=ft.Text(title or "Visualizar Imagem"),
            content=ft.Container(
                content=ft.Image(src=image_src, fit=ft.ImageFit.CONTAIN),
                width=700,
                height=600,
            ),
            actions=[ft.TextButton("Fechar", on_click=close_modal)],
            modal=True,
        )
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()

    def _show_description(self, item: Dict[str, Any]):
        """Show description in a modal with detailed logging"""
        try:
            descricao = item.get('Descrição') or item.get('descricao') or 'Sem descrição dispon��vel'
            nome = item.get('Nome do Carro') or item.get('nome') or 'Anúncio'
            link = item.get('Link') or item.get('link') or ''
            self.append_log(f"Abrindo descrição para: {nome} (link={link})")

            def close_modal(e):
                try:
                    dlg.open = False
                    self.page.update()
                    self.append_log(f"Fechou modal de descrição para: {nome}")
                except Exception as ex:
                    self.append_log(f"Erro ao fechar modal de descrição: {ex}")

            dlg = ft.AlertDialog(
                title=ft.Text(f"Descrição - {nome}"),
                content=ft.Container(
                    content=ft.Column([
                        ft.Text(descricao, selectable=True)
                    ], scroll=ft.ScrollMode.AUTO),
                    width=600,
                    height=500,
                ),
                actions=[ft.TextButton("Fechar", on_click=close_modal)],
                modal=True,
            )
            self._desc_dialog = dlg
            try:
                self.page.dialog = None
            except Exception:
                pass
            self._open_alert_dialog(dlg)
            self.append_log(f"Modal de descrição aberto para: {nome}")
        except Exception as e:
            self.append_log(f"Erro em _show_description: {e}")

    def _build_card(self, item: Dict[str, Any]) -> ft.Card:
        image_src = item.get('Imagem') or item.get('Image') or item.get('foto') or ''
        name = item.get('Nome do Carro') or item.get('nome') or 'Carro'
        valor = item.get('Valor') or item.get('valor') or ''
        link = item.get('Link') or item.get('link') or ''
        portal = item.get('Portal') or item.get('portal') or ''
        motor = item.get('Motor') or item.get('Potência do Motor') or item.get('Potência') or ''

        is_liked = link in self.liked_items
        is_hidden = link in self.hidden_items
        is_best_match = link == self.best_match_link
        # simple display: prefix name with emoji when best match
        name_display = f"✨ {name}" if is_best_match else name
        try:
            self.append_log(f"Construindo card - nome={name} link={link} best_match={is_best_match} liked={is_liked} hidden={is_hidden}")
        except Exception:
            pass

        destaque = [
            ("Ano", "Ano"),
            ("KM", "KM"),
            ("Portas", "Portas"),
            ("Combustível", "Combustível")
        ]
        detalhes_html = []
        if motor:
            detalhes_html.append(ft.Text(f"Potência: {motor}", size=12, weight="bold", color="#2563EB"))
        for label, key in destaque:
            val = item.get(key) or item.get(key.lower())
            if val:
                detalhes_html.append(ft.Text(f"{label}: {val}", size=12, weight="bold", color="#2563EB"))

        def openlink():
            if link:
                try:
                    self.page.launch_url(link)
                except Exception as ex:  # ← ADICIONE ESTA LINHA
                    self.append_log(f"Erro ao abrir link: {ex}")  # ← E ESTA


        def on_image_click(e):
            self._show_image_preview(image_src, name)

        image_container = ft.GestureDetector(
            content=ft.Image(src=image_src, width=120, height=90, fit=ft.ImageFit.COVER),
            on_tap=on_image_click,
        )

        # build the main content row for the card
        content_row = ft.Row([
            image_container,
            ft.Column([
                ft.Text(name_display, size=14, weight="bold"),
                ft.Text(valor, size=12, color="#16a34a"),
                ft.Text(f"📍 {portal}", size=10, color="#424242") if portal else ft.Container(height=0),
                ft.Column(detalhes_html) if detalhes_html else ft.Container(height=0),
                ft.Row([
                    ft.ElevatedButton("Abrir", on_click=lambda e: openlink(), height=28),
                    ft.TextButton("Descrição", on_click=lambda e, item=item: self._show_description(item)),
                    ft.IconButton(
                        icon=ft.Icons.FAVORITE if is_liked else ft.Icons.FAVORITE_BORDER,
                        icon_color="#e91e63" if is_liked else None,
                        on_click=lambda e, item=item: self._toggle_like(item),
                        tooltip="Curtir" if not is_liked else "Descurtir",
                        height=28
                    ),
                    ft.IconButton(
                        icon=ft.Icons.VISIBILITY_OFF if is_hidden else ft.Icons.VISIBILITY,
                        icon_color="#ff6b6b" if is_hidden else None,
                        on_click=lambda e, item=item: self._toggle_hide(item),
                        tooltip="Ocultar" if not is_hidden else "Mostrar",
                        height=28
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color="#dc2626",
                        on_click=lambda e, item=item: self._remove_item(item),
                        tooltip="Remover permanentemente",
                        height=28
                    )
                ], spacing=4)
            ], spacing=4, expand=True)
        ], alignment=ft.MainAxisAlignment.START, spacing=12)

        # ultra-simple card container — never apply background or big borders
        container = ft.Container(
            content_row,
            padding=10,
            border=None,
            border_radius=8,
        )
        return ft.Card(container, expand=False)


    def load_state(self):
        try:
            state = load_app_state()
            if state:
                self.results = state.get('results', [])
                self.filtered_results = state.get('filtered_results', [])
                self.liked_items = set(state.get('liked_items', []))
                self.hidden_items = set(state.get('hidden_items', []))
                self.ranking_list = state.get('ranking_list', [])
                self.ranking_descriptions = state.get('ranking_descriptions', {})
                self.liked_items_cache = state.get('liked_items_cache', {})
                self.scraping_speed = state.get('scraping_speed', 'baixo')
                self.preferences = state.get('preferences', self.preferences)
                self.preference_order = state.get('preference_order', self.preference_order)
        except Exception as e:
            print(f"Erro ao carregar estado: {e}")

    def save_state(self):
        try:
            state = {
                'results': self.results,
                'filtered_results': self.filtered_results,
                'liked_items': list(self.liked_items),
                'hidden_items': list(self.hidden_items),
                'ranking_list': self.ranking_list,
                'ranking_descriptions': self.ranking_descriptions,
                'preferences': self.preferences,
                'preference_order': self.preference_order,
            }
            save_app_state(state)
        except Exception as e:
            print(f"Erro ao salvar estado: {e}")

    def on_stop(self, e):
        self.append_log("Solicitando parada do scraper...")
        self.add_loading_log("Parada solicitada pelo usuário")
        self.save_state()
        write_stop_signal()
        try:
            if self.child and self.child.poll() is None:
                try:
                    self.child.kill()
                    self.append_log("Processo Python morto pelo app.")
                except Exception as ex:
                    self.append_log(f"Erro ao matar child process: {ex}")
            else:
                self.append_log("Nenhum processo Python ativo.")
        except Exception as ex:
            self.append_log(f"Erro ao tentar parar: {ex}")
        self.stop_btn.disabled = True
        self.start_btn.disabled = False
        self.hide_loading_screen()
        self.page.update()

    def on_import(self, e):
        if pd is None:
            self.append_log("Pandas não instalado; import desabilitado.")
            return
        def pick_result(e: ft.FilePickerResultEvent):
            if not e.files:
                return
            f = e.files[0]
            try:
                path = f.path
                if path.lower().endswith('.csv'):
                    df = pd.read_csv(path)
                else:
                    df = pd.read_excel(path)
                self.results = df.to_dict(orient='records')
                self.filtered_results = self.results.copy()
                self.refresh_results_table()
                self.append_log(f"Importado {len(self.results)} registros de {path}")
                self.export_btn.disabled = False
            except Exception as ex:
                self.append_log(f"Erro ao importar arquivo: {ex}")
        fp = ft.FilePicker(on_result=pick_result)
        self.page.dialog = fp
        fp.pick_files(allow_multiple=False)

    def on_export(self, e):
        if pd is None:
            self.append_log("Pandas não instalado; export desabilitado.")
            return
        if not self.results:
            self.append_log("Sem resultados para exportar")
            return
        try:
            df = pd.DataFrame(self.results)
            fname = os.path.join(os.getcwd(), "anuncios_carros_flet.xlsx")
            df.to_excel(fname, index=False)
            self.append_log(f"Exportado Excel para {fname}")
        except Exception as ex:
            self.append_log(f"Erro ao exportar Excel: {ex}")


def main(page: ft.Page):
    ScraperApp(page)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            filters = json.loads(sys.argv[1])
            main_scraper(filters)
        except Exception as e:
            print(f"ERRO: {e}")
            import traceback
            traceback.print_exc()
    else:
        ft.app(target=main)
