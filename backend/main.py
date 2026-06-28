from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from pathlib import Path
import json
from typing import List, Dict
import math
import asyncio
import json as _json
import re
import unicodedata
from . import fetcher

BASE = Path(__file__).resolve().parents[1]

TEAM_CODE_MAP = {
    'brasil': 'BRA',
    'estadosunidos': 'USA',
    'coreadelsur': 'KOR',
    'korearepublic': 'KOR',
    'southkorea': 'KOR',
    'curazao': 'CUW',
    'alemania': 'GER',
    'turquía': 'TUR',
    'turquia': 'TUR',
    'escocia': 'SCO',
    'jordania': 'JOR',
    'uruguay': 'URU',
    'marruecos': 'MAR',
    'túnez': 'TUN',
    'tunez': 'TUN',
    'caboverde': 'CPV',
    'capeverde': 'CPV',
    'capeverdeislands': 'CPV',
    'inglaterra': 'ENG',
    'ecuador': 'ECU',
    'bosnia': 'BIH',
    'bosniaherzegovina': 'BIH',
    'bosniaandherzegovina': 'BIH',
    'bosniayherzegovina': 'BIH',
    'rcongo': 'COD',
    'repcongo': 'COD',
    'rdcongo': 'COD',
    'republicadelcongo': 'COD',
    'espana': 'ESP',
    'españa': 'ESP',
    'austria': 'AUT',
    'australia': 'AUS',
    'qatar': 'QAT',
    'bélgica': 'BEL',
    'belgica': 'BEL',
    'suiza': 'SUI',
    'egipto': 'EGY',
    'uzbekistán': 'UZB',
    'uzbekistan': 'UZB',
    'paísesbajos': 'NED',
    'paisesbajos': 'NED',
    'netherlands': 'NED',
    'holanda': 'NED',
    'colombia': 'COL',
    'ghana': 'GHA',
    'irak': 'IRQ',
    'francia': 'FRA',
    'méxico': 'MEX',
    'mexico': 'MEX',
    'suecia': 'SWE',
    'arabiasaudita': 'KSA',
    'portugal': 'POR',
    'senegal': 'SEN',
    'chequia': 'CZE',
    'czechrepublic': 'CZE',
    'czechia': 'CZE',
    'panamá': 'PAN',
    'panama': 'PAN',
    'noruega': 'NOR',
    'canadá': 'CAN',
    'canada': 'CAN',
    'sudáfrica': 'RSA',
    'sudafrica': 'RSA',
    'southafrica': 'RSA',
    'argelia': 'DZA',
    'argentina': 'ARG',
    'japón': 'JPN',
    'japon': 'JPN',
    'nuevazelanda': 'NZL',
    'haití': 'HAI',
    'haiti': 'HAI',
    'croacia': 'CRO',
    'costademarfil': 'CIV',
    'cotedivoire': 'CIV',
    'ivorycoast': 'CIV',
    'paraguay': 'PAR',
    'irán': 'IRN',
    'iran': 'IRN',
}


def normalize_team_name(name: str) -> str:
    clean = unicodedata.normalize('NFKD', name or '')
    clean = ''.join(ch for ch in clean if not unicodedata.combining(ch))
    clean = re.sub(r'[^a-z0-9]', '', clean.casefold())
    return clean


def canonical_team_id(name: str) -> str:
    norm = normalize_team_name(name)
    if norm in TEAM_CODE_MAP:
        return TEAM_CODE_MAP[norm]
    if len(name or '') == 3 and (name or '').isalpha():
        return (name or '').upper()
    return norm
DATA_DIR = BASE / 'data'
PICKS_FILE = DATA_DIR / 'picks.json'
RESULTS_FILE = DATA_DIR / 'results.json'
ELIMINATED_FILE = DATA_DIR / 'eliminated.json'
FRONTEND_DIR = BASE / 'frontend'

app = FastAPI(title='Quiniela Mundial 2026 API')
app.mount('/static', StaticFiles(directory=FRONTEND_DIR), name='static')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResultIn(BaseModel):
    match: str
    team_a: str
    team_b: str
    score_a: int
    score_b: int


def load_picks():
    with open(PICKS_FILE, encoding='utf-8') as f:
        return json.load(f)


def load_results():
    if not RESULTS_FILE.exists():
        return []
    with open(RESULTS_FILE, encoding='utf-8') as f:
        return json.load(f)


def load_eliminated():
    if not ELIMINATED_FILE.exists():
        return []
    with open(ELIMINATED_FILE, encoding='utf-8') as f:
        try:
            return json.load(f)
        except Exception:
            return []


def save_eliminated(items):
    with open(ELIMINATED_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def save_results(results):
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def update_eliminated_if_round32(results: List[Dict]):
    """If Round of 32 participants are present in results, mark all teams
    that are NOT in the Round of 32 as eliminated and persist to file.
    Returns the new eliminated list when updated, otherwise None."""
    all_teams = set()
    r32_teams = set()
    for r in results:
        ta = r.get('team_a')
        tb = r.get('team_b')
        if ta:
            all_teams.add(ta)
        if tb:
            all_teams.add(tb)
        # try to detect stage/round labels that indicate Round of 32
        stage = ''
        for k in ('stage', 'round', 'match'):
            if r.get(k):
                stage = str(r.get(k)).lower()
                break
        if '32' in stage or 'round of 32' in stage or 'r32' in stage:
            if ta:
                r32_teams.add(ta)
            if tb:
                r32_teams.add(tb)

    # If we have at least 32 distinct teams in Round of 32, consider the phase reached
    if len(r32_teams) >= 32:
        eliminated = sorted(list(all_teams - r32_teams))
        save_eliminated(eliminated)
        return eliminated
    return None


_FINISHED_STATUSES = {'FT', 'FINISHED', 'FINAL', 'COMPLETED', 'FT_PEN', 'AET'}

# ── BRACKET ──────────────────────────────────────────────────────────────────
GROUPS = list('ABCDEFGHIJKL')

# Round of 32 — orden real del bracket FIFA 2026 (por ID de partido en football-data.org)
# Confirmados con la API: posiciones 0,2,3,6,8,9
# W_X=ganador grupo X, R_X=subcampeón grupo X, T{n}=n-ésimo mejor 3ro
R32_MATCHES = [
    ('W_E', '3rd_D'), # 537415 Jun29: Alemania vs Paraguay ✓
    ('W_H', 'R_J'),   # 537416 Jun30: España vs Austria ✓
    ('R_A', 'R_B'),   # 537417 Jun28: Sudáfrica vs Canadá ✓
    ('W_F', 'R_C'),   # 537418 Jun30: Países Bajos vs Marruecos ✓
    ('R_K', 'R_L'),   # 537419 Jul02: Portugal vs Croacia ✓
    ('W_G', 'T1'),    # 537420 Jul02: Bélgica vs mejor 3ro no asignado
    ('W_D', '3rd_B'), # 537421 Jul02: USA vs Bosnia ✓
    ('W_K', '3rd_L'), # 537422 Jul01: Colombia vs Ghana ✓
    ('W_C', 'R_F'),   # 537423 Jun29: Brasil vs Japón ✓
    ('R_I', 'R_E'),   # 537424 Jun30: Noruega vs Costa de Marfil ✓
    ('W_A', 'T2'),    # 537425 Jul01: México vs 2do mejor 3ro
    ('W_I', '3rd_F'), # 537426 Jul01: Francia vs Suecia ✓
    ('W_J', 'R_H'),   # 537427 Jul03: Argentina vs Cabo Verde ✓
    ('R_D', 'R_G'),   # 537428 Jul03: Australia vs Egipto ✓
    ('W_B', 'T3'),    # 537429 Jul03: Suiza vs 3er mejor 3ro
    ('W_L', 'T4'),    # 537430 Jul04: Inglaterra vs 4to mejor 3ro
]


def compute_qualifiers(groups_data: list) -> dict:
    group_map = {g['group']: g['standings'] for g in groups_data}
    qualifiers: Dict[str, str] = {}
    thirds = []

    # Groups whose 3rd-place team is already assigned to a specific bracket slot
    specific_3rd_groups = {slot[4:] for home, away in R32_MATCHES for slot in (home, away) if slot.startswith('3rd_')}

    all_groups_complete = True
    for letter in GROUPS:
        gname = f'Group {letter}'
        standings = group_map.get(gname, [])
        complete = len(standings) >= 4 and all(t.get('played', 0) >= 3 for t in standings[:4])
        if not complete:
            all_groups_complete = False

        qualifiers[f'W_{letter}'] = standings[0]['team'] if complete and len(standings) >= 1 else None
        qualifiers[f'R_{letter}'] = standings[1]['team'] if complete and len(standings) >= 2 else None
        # 3rd_X = specific third-place team from group X (for known bracket assignments)
        qualifiers[f'3rd_{letter}'] = standings[2]['team'] if complete and len(standings) >= 3 else None

        if complete and len(standings) >= 3:
            t = standings[2]
            thirds.append({'group': letter, 'team': t['team'], 'points': t['points'], 'gd': t['gd'], 'gf': t['gf']})

    # Only assign T-slots when ALL 12 groups are done — until then we can't
    # know which 8 third-place teams actually qualify.
    # Exclude groups already assigned via specific 3rd_X codes so teams don't appear twice.
    if all_groups_complete:
        generic_thirds = [t for t in thirds if t['group'] not in specific_3rd_groups]
        generic_thirds.sort(key=lambda x: (-x['points'], -x['gd'], -x['gf']))
        for i in range(8):
            qualifiers[f'T{i+1}'] = generic_thirds[i]['team'] if i < len(generic_thirds) else None
    else:
        for i in range(8):
            qualifiers[f'T{i+1}'] = None

    return qualifiers


def find_ko_winner(team_a: str, team_b: str, results: list) -> str:
    if not team_a or not team_b:
        return None
    na, nb = normalize_team_name(team_a), normalize_team_name(team_b)
    for r in results:
        # skip group stage matches — they have a 'group' field
        if r.get('group'):
            continue
        ra = normalize_team_name(r.get('team_a', ''))
        rb = normalize_team_name(r.get('team_b', ''))
        if (ra == na and rb == nb) or (ra == nb and rb == na):
            if str(r.get('status', '')).upper() not in _FINISHED_STATUSES:
                continue
            sa, sb = r.get('score_a', 0), r.get('score_b', 0)
            if sa > sb:
                return r['team_a']
            if sb > sa:
                return r['team_b']
    return None


def compute_bracket(results: list, picks: list, groups_data: list) -> dict:
    qualifiers = compute_qualifiers(groups_data)
    picks_map: Dict[str, str] = {}
    for p in picks:
        for team in p.get('teams', []):
            picks_map[team] = p['name']

    def participant(team):
        return picks_map.get(team, '') if team else ''

    def make_match(rid, num, slot_a, slot_b, team_a=None, team_b=None):
        ta = team_a if team_a is not None else qualifiers.get(slot_a)
        tb = team_b if team_b is not None else qualifiers.get(slot_b)
        winner = find_ko_winner(ta, tb, results)
        return {
            'id': f'{rid}_{num}',
            'slot_a': slot_a, 'slot_b': slot_b,
            'team_a': ta, 'team_b': tb,
            'winner': winner,
            'participant_a': participant(ta),
            'participant_b': participant(tb),
        }

    r32 = [make_match('r32', i + 1, sa, sb) for i, (sa, sb) in enumerate(R32_MATCHES)]

    r16 = []
    for i in range(8):
        pa, pb = r32[i * 2], r32[i * 2 + 1]
        r16.append(make_match('r16', i + 1, f'W_R32_{i*2+1}', f'W_R32_{i*2+2}',
                              pa['winner'], pb['winner']))

    qf = []
    for i in range(4):
        pa, pb = r16[i * 2], r16[i * 2 + 1]
        qf.append(make_match('qf', i + 1, f'W_R16_{i*2+1}', f'W_R16_{i*2+2}',
                             pa['winner'], pb['winner']))

    sf = []
    for i in range(2):
        pa, pb = qf[i * 2], qf[i * 2 + 1]
        sf.append(make_match('sf', i + 1, f'W_QF_{i*2+1}', f'W_QF_{i*2+2}',
                             pa['winner'], pb['winner']))

    final = make_match('final', 1, 'W_SF_1', 'W_SF_2', sf[0]['winner'], sf[1]['winner'])

    return {
        'rounds': [
            {'id': 'r32',   'name': 'Ronda de 32',      'matches': r32},
            {'id': 'r16',   'name': 'Octavos de Final', 'matches': r16},
            {'id': 'qf',    'name': 'Cuartos de Final', 'matches': qf},
            {'id': 'sf',    'name': 'Semifinales',      'matches': sf},
            {'id': 'final', 'name': 'Final',            'matches': [final]},
        ],
        'champion': final['winner'],
    }


def is_finished_match(r: Dict) -> bool:
    """Return True only for officially finished matches.
    Results without a status field are treated as finished (backward compat)."""
    status = r.get('status')
    if status is None:
        return True
    return str(status).upper() in _FINISHED_STATUSES


def compute_team_points(results: List[Dict]) -> Dict[str, int]:
    points = {}
    for r in results:
        if not is_finished_match(r):
            continue
        a = canonical_team_id(r['team_a'])
        b = canonical_team_id(r['team_b'])
        sa = r['score_a']
        sb = r['score_b']
        if sa > sb:
            pts_a, pts_b = 3, 0
        elif sa < sb:
            pts_a, pts_b = 0, 3
        else:
            pts_a, pts_b = 1, 1
        points[a] = points.get(a, 0) + pts_a
        points[b] = points.get(b, 0) + pts_b
    return points


def compute_standings(picks, results):
    team_points = compute_team_points(results)
    standings = []
    for p in picks:
        score = 0
        for t in p.get('teams', []):
            score += team_points.get(canonical_team_id(t), 0)
        standings.append({'name': p.get('name'), 'score': score})
    # compute probabilities with softmax on scores
    exps = [math.exp(s['score']) for s in standings]
    s = sum(exps) if sum(exps) > 0 else 1
    for i, st in enumerate(standings):
        st['probability'] = round(100 * exps[i] / s, 2)
    standings.sort(key=lambda x: x['score'], reverse=True)
    return standings


def compute_group_standings(results: List[Dict]):
    groups = {}
    live_by_group: Dict[str, int] = {}
    for r in results:
        group_name = r.get('group')
        if not group_name:
            continue
        sa = r.get('score_a')
        sb = r.get('score_b')
        if sa is None or sb is None:
            continue
        ta = r['team_a']
        tb = r['team_b']
        groups.setdefault(group_name, {})
        group = groups[group_name]
        for team in (ta, tb):
            if team not in group:
                group[team] = {'team': team, 'points': 0, 'gf': 0, 'ga': 0, 'gd': 0, 'played': 0}
        if not is_finished_match(r):
            # track in-progress matches per group but don't add points
            live_by_group[group_name] = live_by_group.get(group_name, 0) + 1
            continue
        group[ta]['played'] += 1
        group[tb]['played'] += 1
        group[ta]['gf'] += sa
        group[ta]['ga'] += sb
        group[tb]['gf'] += sb
        group[tb]['ga'] += sa
        group[ta]['gd'] = group[ta]['gf'] - group[ta]['ga']
        group[tb]['gd'] = group[tb]['gf'] - group[tb]['ga']
        if sa > sb:
            group[ta]['points'] += 3
        elif sa < sb:
            group[tb]['points'] += 3
        else:
            group[ta]['points'] += 1
            group[tb]['points'] += 1

    result = []
    for group_name, teams in groups.items():
        standings = sorted(
            teams.values(),
            key=lambda x: (-x['points'], -x['gd'], -x['gf'], x['team'])
        )
        entry = {'group': group_name, 'standings': standings}
        if live_by_group.get(group_name):
            entry['live_matches'] = live_by_group[group_name]
        result.append(entry)
    result.sort(key=lambda x: x['group'])
    return result


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        to_remove = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                to_remove.append(connection)
        for c in to_remove:
            self.disconnect(c)


manager = ConnectionManager()


@app.get('/api/picks')
def api_picks():
    return load_picks()


@app.get('/api/results')
def api_results():
    return load_results()


@app.get('/api/eliminated')
def api_eliminated():
    return load_eliminated()


@app.post('/api/eliminated')
def post_eliminated(payload: Dict):
    """POST body can be {"teams": [..]} to replace the list,
    or {"team": "Name"} to toggle a single team in the eliminated list."""
    current = set(load_eliminated())
    teams = payload.get('teams') if isinstance(payload, dict) else None
    team = payload.get('team') if isinstance(payload, dict) else None
    if teams is not None:
        # replace list
        out = list(dict.fromkeys([t for t in teams if t]))
        save_eliminated(out)
        return {'ok': True, 'eliminated': out}
    if team is not None:
        if team in current:
            current.remove(team)
        else:
            current.add(team)
        out = list(sorted(current))
        save_eliminated(out)
        return {'ok': True, 'eliminated': out}
    raise HTTPException(status_code=400, detail='expected {teams:[..]} or {team:"name"}')


@app.get('/api/standings')
def api_standings():
    picks = load_picks()
    results = load_results()
    return compute_standings(picks, results)


@app.get('/api/groups')
def api_groups():
    results = load_results()
    return compute_group_standings(results)


@app.get('/api/bracket')
def api_bracket():
    results = load_results()
    picks = load_picks()
    groups_data = compute_group_standings(results)
    return compute_bracket(results, picks, groups_data)


@app.post('/api/result')
async def post_result(r: ResultIn):
    results = load_results()
    entry = r.dict()
    entry['id'] = len(results) + 1
    results.append(entry)
    save_results(results)
    # broadcast updated standings
    picks = load_picks()
    standings = compute_standings(picks, results)
    groups = compute_group_standings(results)
    # compute automatic eliminated teams if Round of 32 is present
    updated = update_eliminated_if_round32(results)
    import asyncio
    asyncio.create_task(manager.broadcast({'type': 'update', 'results': results, 'standings': standings, 'groups': groups, 'eliminatedTeams': load_eliminated()}))
    return {'ok': True, 'result': entry}


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # on connect, send current state
        picks = load_picks()
        results = load_results()
        standings = compute_standings(picks, results)
        await websocket.send_json({'type': 'init', 'results': results, 'standings': standings, 'groups': compute_group_standings(results), 'picks_count': len(picks), 'eliminatedTeams': load_eliminated()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post('/api/refresh')
async def api_refresh():
    """Trigger an immediate fetch from the external API and broadcast updates."""
    cfg_path = BASE / 'backend' / 'config.json'
    import os
    try:
        with open(cfg_path, encoding='utf-8') as f:
            cfg = _json.load(f)
    except Exception:
        cfg = {}
    if os.environ.get('FOOTBALL_API_KEY'):
        cfg['api_key'] = os.environ['FOOTBALL_API_KEY']
    if os.environ.get('FOOTBALL_API_URL'):
        cfg['api_url'] = os.environ['FOOTBALL_API_URL']
    if os.environ.get('FOOTBALL_API_MODE'):
        cfg['mode'] = os.environ['FOOTBALL_API_MODE']
    if not cfg.get('mode'):
        cfg['mode'] = 'api'
    if not cfg.get('api_url'):
        cfg['api_url'] = 'https://api.football-data.org/v4/competitions/WC/matches'
    try:
        added = await fetcher.fetch_and_update(cfg)
        picks = load_picks()
        results = load_results()
        standings = compute_standings(picks, results)
        groups = compute_group_standings(results)
        if added:
            try:
                update_eliminated_if_round32(results)
            except Exception:
                pass
            asyncio.create_task(manager.broadcast({'type': 'update', 'results': results, 'standings': standings, 'groups': groups, 'eliminatedTeams': load_eliminated()}))
        return {'ok': True, 'added': added, 'total': len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/', response_class=FileResponse)
def root():
    return FRONTEND_DIR / 'index.html'

@app.on_event('startup')
async def start_background_fetcher():
    import os
    cfg_path = BASE / 'backend' / 'config.json'
    try:
        with open(cfg_path, encoding='utf-8') as f:
            cfg = _json.load(f)
        print('[fetcher] config.json cargado')
    except Exception:
        cfg = {}
        print('[fetcher] config.json no encontrado, usando variables de entorno')

    if os.environ.get('FOOTBALL_API_KEY'):
        cfg['api_key'] = os.environ['FOOTBALL_API_KEY']
        print('[fetcher] FOOTBALL_API_KEY cargada desde entorno')
    if os.environ.get('FOOTBALL_API_URL'):
        cfg['api_url'] = os.environ['FOOTBALL_API_URL']
    if os.environ.get('FOOTBALL_API_MODE'):
        cfg['mode'] = os.environ['FOOTBALL_API_MODE']

    if not cfg.get('mode'):
        cfg['mode'] = 'api'
    if not cfg.get('api_url'):
        cfg['api_url'] = 'https://api.football-data.org/v4/competitions/WC/matches'

    has_key = bool(cfg.get('api_key'))
    print(f'[fetcher] modo={cfg["mode"]} url={cfg["api_url"]} api_key={"OK" if has_key else "FALTA"}')

    interval = int(cfg.get('interval_seconds', 120))

    async def looper():
        while True:
            try:
                added = await fetcher.fetch_and_update(cfg)
                print(f'[fetcher] fetch completado — {added} nuevos resultados')
                if added:
                    picks = load_picks()
                    results = load_results()
                    standings = compute_standings(picks, results)
                    try:
                        update_eliminated_if_round32(results)
                    except Exception:
                        pass
                    await manager.broadcast({'type': 'update', 'results': results, 'standings': standings, 'groups': compute_group_standings(results), 'eliminatedTeams': load_eliminated()})
            except Exception as e:
                print(f'[fetcher] ERROR: {e}')
            await asyncio.sleep(interval)

    asyncio.create_task(looper())


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    uvicorn.run('backend.main:app', host='0.0.0.0', port=port, reload=True)
