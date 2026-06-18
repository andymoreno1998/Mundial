import httpx
import csv
import io
import re
from bs4 import BeautifulSoup
from pathlib import Path
import json
from typing import List, Dict

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / 'data'
RESULTS_FILE = DATA_DIR / 'results.json'


def load_results():
    if not RESULTS_FILE.exists():
        return []
    with open(RESULTS_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_results(results):
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def _norm_key(name: str) -> str:
    """Normalize team name for deduplication: strip accents, lowercase, no spaces."""
    import unicodedata as _ud
    s = _ud.normalize('NFKD', name or '')
    s = ''.join(c for c in s if not _ud.combining(c))
    return re.sub(r'[^a-z0-9]', '', s.casefold())


def merge_results(new: List[Dict]):
    existing = load_results()
    # Key by normalized team pair so "Mexico"/"México" are treated as the same
    existing_map = {
        (_norm_key(r.get('team_a', '')), _norm_key(r.get('team_b', ''))): i
        for i, r in enumerate(existing)
    }
    added = 0
    for r in new:
        key = (_norm_key(r.get('team_a', '')), _norm_key(r.get('team_b', '')))
        if key in existing_map:
            idx = existing_map[key]
            changed = False
            new_status = r.get('status')
            if new_status and existing[idx].get('status') != new_status:
                existing[idx]['status'] = new_status
                changed = True
            for score_key in ('score_a', 'score_b'):
                if r.get(score_key) is not None and existing[idx].get(score_key) != r[score_key]:
                    existing[idx][score_key] = r[score_key]
                    changed = True
            if changed:
                added += 1
        else:
            r['id'] = len(existing) + 1
            existing.append(r)
            existing_map[key] = len(existing) - 1
            added += 1
    if added:
        save_results(existing)
    return added


async def fetch_from_google_sheet(sheet_csv_url: str) -> List[Dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(sheet_csv_url, timeout=20)
        r.raise_for_status()
        text = r.text
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        # expect columns: match,team_a,team_b,score_a,score_b[,status,group]
        try:
            raw_status = (row.get('status') or row.get('Status') or row.get('Estado') or '').strip().upper()
            raw_a = (row.get('team_a') or row.get('Team A') or row.get('Equipo A') or '').strip()
            raw_b = (row.get('team_b') or row.get('Team B') or row.get('Equipo B') or '').strip()
            entry = {
                'match': row.get('match') or row.get('Match') or row.get('Partido') or '',
                'team_a': _translate_team(raw_a),
                'team_b': _translate_team(raw_b),
                'score_a': int(row.get('score_a') or row.get('Score A') or row.get('Goles A') or 0),
                'score_b': int(row.get('score_b') or row.get('Score B') or row.get('Goles B') or 0),
                'status': raw_status if raw_status else 'FT',
            }
            group = row.get('group') or row.get('Group') or row.get('Grupo') or ''
            if group:
                entry['group'] = group.strip()
            out.append(entry)
        except Exception:
            continue
    return out


# football-data.org English name → Spanish name used in picks.json
_EN_TO_ES: Dict[str, str] = {
    'Brazil': 'Brasil',
    'United States': 'Estados Unidos',
    'Korea Republic': 'Corea del Sur',
    'South Korea': 'Corea del Sur',
    'Curaçao': 'Curazao',
    'Germany': 'Alemania',
    'Turkey': 'Turquía',
    'Türkiye': 'Turquía',
    'Scotland': 'Escocia',
    'Jordan': 'Jordania',
    'Uruguay': 'Uruguay',
    'Morocco': 'Marruecos',
    'Tunisia': 'Túnez',
    'Cape Verde': 'Cabo Verde',
    'Cape Verde Islands': 'Cabo Verde',
    'England': 'Inglaterra',
    'Ecuador': 'Ecuador',
    'Bosnia and Herzegovina': 'Bosnia',
    'Bosnia & Herzegovina': 'Bosnia',
    'Bosnia-Herzegovina': 'Bosnia',
    'Bosnia y Herzegovina': 'Bosnia',
    'Bosnia Herzegovina': 'Bosnia',
    'DR Congo': 'R. Congo',
    'Congo DR': 'R. Congo',
    'Democratic Republic of Congo': 'R. Congo',
    'Rep. Congo': 'R. Congo',
    'RD Congo': 'R. Congo',
    'República del Congo': 'R. Congo',
    'RD del Congo': 'R. Congo',
    'Spain': 'España',
    'Austria': 'Austria',
    'Australia': 'Australia',
    'Qatar': 'Qatar',
    'Belgium': 'Bélgica',
    'Switzerland': 'Suiza',
    'Egypt': 'Egipto',
    'Uzbekistan': 'Uzbekistán',
    'Netherlands': 'Países Bajos',
    'Colombia': 'Colombia',
    'Ghana': 'Ghana',
    'Iraq': 'Irak',
    'France': 'Francia',
    'Mexico': 'México',
    'Sweden': 'Suecia',
    'Saudi Arabia': 'Arabia Saudita',
    'Portugal': 'Portugal',
    'Senegal': 'Senegal',
    'Czechia': 'Chequia',
    'Czech Republic': 'Chequia',
    'Panama': 'Panamá',
    'Norway': 'Noruega',
    'Canada': 'Canadá',
    'South Africa': 'Sudáfrica',
    'Algeria': 'Argelia',
    'Argentina': 'Argentina',
    'Japan': 'Japón',
    'New Zealand': 'Nueva Zelanda',
    'Haiti': 'Haití',
    'Croatia': 'Croacia',
    "Côte d'Ivoire": 'Costa de Marfil',
    'Ivory Coast': 'Costa de Marfil',
    'Paraguay': 'Paraguay',
    'Iran': 'Irán',
    'Islamic Republic of Iran': 'Irán',
}

_FD_STATUS_MAP: Dict[str, str] = {
    'FINISHED': 'FT',
    'AWARDED': 'FT',
    'IN_PLAY': 'LIVE',
    'PAUSED': 'HT',
    'HALF_TIME': 'HT',
    'EXTRA_TIME': 'LIVE',
    'PENALTY_SHOOTOUT': 'LIVE',
    'SUSPENDED': 'HT',
}


def _translate_team(name: str) -> str:
    return _EN_TO_ES.get(name, name)


def _format_group(raw: str) -> str:
    """Convert 'GROUP_A' → 'Group A', pass through anything else."""
    if raw and raw.upper().startswith('GROUP_'):
        letter = raw.split('_', 1)[1].upper()
        return f'Group {letter}'
    return raw or ''


async def fetch_from_api(api_url: str, api_key: str = None) -> List[Dict]:
    headers = {}
    if api_key:
        # football-data.org uses X-Auth-Token, not Bearer
        headers['X-Auth-Token'] = api_key
    async with httpx.AsyncClient() as client:
        r = await client.get(api_url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    out = []
    matches = data.get('matches') if isinstance(data, dict) else None
    if not matches or not isinstance(matches, list):
        return out
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)

    for m in matches:
        try:
            raw_status = (m.get('status') or '').strip().upper()
            utc_date = m.get('utcDate') or ''

            # If still TIMED/SCHEDULED but kickoff time has passed, treat as live
            if raw_status in ('TIMED', 'SCHEDULED') and utc_date:
                try:
                    kickoff = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
                    if kickoff <= now_utc:
                        raw_status = 'IN_PLAY'
                except Exception:
                    pass

            if raw_status in ('SCHEDULED', 'TIMED', 'POSTPONED', 'CANCELLED', ''):
                continue
            status = _FD_STATUS_MAP.get(raw_status, raw_status) or None

            score = m.get('score') or {}
            ft = score.get('fullTime') or {}
            score_a = ft.get('home')
            score_b = ft.get('away')
            # fullTime is null during live matches; fall back to halfTime score
            if score_a is None or score_b is None:
                ht = score.get('halfTime') or {}
                score_a = ht.get('home')
                score_b = ht.get('away')
            # for live/HT matches still without score, default to 0-0 so entry is tracked
            if score_a is None:
                score_a = 0
            if score_b is None:
                score_b = 0

            team_a_raw = (m.get('homeTeam') or {}).get('name') or ''
            team_b_raw = (m.get('awayTeam') or {}).get('name') or ''
            team_a = _translate_team(team_a_raw)
            team_b = _translate_team(team_b_raw)

            matchday = m.get('matchday') or ''
            group_raw = m.get('group') or ''
            group = _format_group(group_raw)
            match_label = f'Jornada {matchday}' if matchday else (group or 'Mundial 2026')

            entry: Dict = {
                'match': match_label,
                'team_a': team_a,
                'team_b': team_b,
                'score_a': int(score_a),
                'score_b': int(score_b),
            }
            if group:
                entry['group'] = group
            if status:
                entry['status'] = status
            out.append(entry)
        except Exception:
            continue
    return out


async def fetch_from_fifa_page(url: str) -> List[Dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        html = r.text

    # Try to parse structured HTML first
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')

    # Regex to find patterns like "Team A 1 - 0 Team B" or with en dash
    pattern = re.compile(r"([\wÀ-ÿ .']{2,40})\s+(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+([\wÀ-ÿ .']{2,40})")
    found = []
    for m in pattern.finditer(text):
        ta = m.group(1).strip()
        sa = int(m.group(2))
        sb = int(m.group(3))
        tb = m.group(4).strip()
        found.append({'match': f"{ta} vs {tb}", 'team_a': ta, 'team_b': tb, 'score_a': sa, 'score_b': sb})

    # Deduplicate by team pairs
    out = []
    seen = set()
    for e in found:
        key = (e['team_a'], e['team_b'], e['score_a'], e['score_b'])
        if key not in seen:
            out.append(e)
            seen.add(key)
    return out


async def fetch_from_fifa_playwright(url: str) -> List[Dict]:
    # Use Playwright to render JS and extract match scores and group data from the FIFA page
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(8000)

        anchors = await page.query_selector_all('a[href*="/match-centre/match/"]')
        out = []
        seen = set()
        for anchor in anchors:
            text = await anchor.inner_text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if len(lines) < 5:
                continue

            team_a = lines[0]
            score_a = None
            score_b = None
            team_b = None
            group_name = None
            match_status = None

            if len(lines) >= 5 and lines[1].isdigit():
                if len(lines) >= 6 and (re.match(r"^\d{1,2}[: ]", lines[2]) or lines[2].endswith("'") or lines[2] in ['FT', 'HT']):
                    if lines[3].isdigit():
                        score_a = int(lines[1])
                        score_b = int(lines[3])
                        team_b = lines[4]
                        indicator = lines[2]
                        if indicator == 'FT':
                            match_status = 'FT'
                        elif indicator == 'HT':
                            match_status = 'HT'
                        else:
                            match_status = indicator  # e.g. "67'" live time
                elif lines[2].isdigit():
                    score_a = int(lines[1])
                    score_b = int(lines[2])
                    team_b = lines[3]
                    match_status = 'FT'  # no indicator = treat as finished

            if score_a is None or score_b is None or not team_b:
                continue

            for value in lines:
                if value.startswith('Group '):
                    group_name = value
                    break

            key = (team_a, team_b, score_a, score_b)
            if key in seen:
                continue
            seen.add(key)
            entry = {
                'match': f"{team_a} vs {team_b}",
                'team_a': team_a,
                'team_b': team_b,
                'score_a': score_a,
                'score_b': score_b,
            }
            if group_name:
                entry['group'] = group_name
            if match_status:
                entry['status'] = match_status
            out.append(entry)

        await browser.close()
        return out


async def fetch_and_update(config: dict) -> int:
    mode = config.get('mode')
    if mode == 'google_sheets':
        url = config.get('sheet_url')
        if not url:
            return 0
        new = await fetch_from_google_sheet(url)
    elif mode == 'api':
        url = config.get('api_url')
        key = config.get('api_key')
        if not url:
            return 0
        new = await fetch_from_api(url, key)
    else:
        # support fifa page scraping or Playwright rendering
        if config.get('mode') == 'fifa' or config.get('fifa_url'):
            url = config.get('fifa_url')
            if not url:
                return 0
            # try lightweight parse first
            new = await fetch_from_fifa_page(url)
            if not new:
                # fallback to Playwright rendering
                try:
                    new = await fetch_from_fifa_playwright(url)
                except Exception:
                    new = []
        elif config.get('mode') == 'playwright' and config.get('fifa_url'):
            url = config.get('fifa_url')
            new = await fetch_from_fifa_playwright(url)
        else:
            return 0
    added = merge_results(new)
    return added
