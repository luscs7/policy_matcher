import os
import json
import base64
from pathlib import Path

import streamlit as st
import pandas as pd
import pydeck as pdk  # já vem com Streamlit

from utils import evaluate_requirements, load_keyword_map

from db import (
    # migrações / boot
    init_db, migrate_db, migrate_accounts, migrate_analytics, 
    log_event, get_analytics, 
    # registro / login
    create_person_account, create_collective_account,
    authenticate_person, authenticate_collective,
    # perfis vinculados à conta (owner)
    save_profile_for_account, update_profile_for_account,
    get_profiles_by_account, load_profile,
)

# ------------------------------------------------------------------
# Boot do banco (uma vez só)
# ------------------------------------------------------------------
init_db()
migrate_db()        # garante created_at/updated_at
migrate_accounts()  # cria accounts e coluna owner_account_id em profiles
migrate_analytics() 

# ------------------------------------------------------------------
# Configuração geral
# ------------------------------------------------------------------
st.set_page_config(page_title="Recomendador de Políticas Públicas", layout="wide")

st.markdown("""
<style>
/* Ajusta largura do container */
.block-container { max-width: 1200px; }
/* Deixa botões um pouco mais altos */
button[kind="primary"] { padding: 0.6rem 1rem; }
</style>
""", unsafe_allow_html=True)

DATA_PATH = os.path.join("data", "politicas_publicas.xlsx")
KW_PATH = "keyword_map.json"
SCHEMA_PATH = "profile_schema.json"

@st.cache_data
def load_data():
    df = pd.read_excel(DATA_PATH, sheet_name=0)
    df.columns = [c.strip() for c in df.columns]
    cols = [
        "Número",
        "Politicas publicas",
        "nivel",
        "Operacionalização/Aplicação",
        "Descrição dos direitos",
        "Acesso",
        "Organização interna (Subprogramas e/ou Eixos)",
        "Link",
        "Observações",
    ]
    keep = [c for c in cols if c in df.columns]
    return df[keep].copy()

@st.cache_resource
def load_configs():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    kw_map = load_keyword_map(KW_PATH)
    return schema, kw_map

df = load_data()
schema, kw_map = load_configs()

@st.cache_data
def load_geo():
    ufs_path = os.path.join("data", "geo", "ufs.csv")
    mun_path = os.path.join("data", "geo", "municipios.csv")
    gj_path  = os.path.join("data", "geo", "municipios_simplificado.geojson")
    ufs = pd.read_csv(ufs_path, dtype={"ibge_uf": str}) if os.path.exists(ufs_path) else pd.DataFrame()
    mun = pd.read_csv(mun_path, dtype={"ibge_mun": str}) if os.path.exists(mun_path) else pd.DataFrame()
    gj  = json.load(open(gj_path, "r", encoding="utf-8")) if os.path.exists(gj_path) else None
    return ufs, mun, gj

ufs_df, mun_df, mun_geojson = load_geo()

# ------------------------------------------------------------------
# Estado de navegação
# ------------------------------------------------------------------
if "page" not in st.session_state:
    st.session_state.page = "home"  # home, policies_overview, profile, matches, policy_detail, policy_picker, auth

if "profile" not in st.session_state:
    st.session_state.profile = {}

if "eligible" not in st.session_state:
    st.session_state.eligible = []  # lista de tuples (idx, met, missing)

if "nearly" not in st.session_state:
    st.session_state.nearly = []    # lista de tuples (idx, met, missing)

if "selected_policy_idx" not in st.session_state:
    st.session_state.selected_policy_idx = None

if "account" not in st.session_state:
    st.session_state.account = None  # dict: {"id":..., "kind": "person"/"collective", ...}

if "current_profile_id" not in st.session_state:
    st.session_state.current_profile_id = None

def goto(page_name: str):
    st.session_state.page = page_name

# ------------------------------------------------------------------
# Helpers de UI / Auth
# ------------------------------------------------------------------
def header_nav(title: str, subtitle: str = ""):
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()

def current_location_from_state():
    """Inferir UF/município do preenchimento atual (mesmo sem login)."""
    uf = st.session_state.get("uf") or st.session_state.profile.get("estado") or None
    mun = st.session_state.get("municipio_select") or st.session_state.profile.get("municipio") or None
    return (uf or "").strip() or None, (mun or "").strip() or None

def current_gender_from_profile():
    """Tenta pegar 'gênero' do perfil (ajuste o nome do campo conforme seu schema)."""
    # troque 'genero' se no seu profile_schema for outro nome (ex.: 'sexo', 'identidade_genero')
    g = st.session_state.profile.get("genero") if isinstance(st.session_state.profile, dict) else None
    return (g or "").strip() or None

def require_login_for_save(msg="Para salvar suas informações, por favor faça login ou crie uma conta."):
    """Se não houver conta logada, avisa e manda para a página de auth."""
    if not st.session_state.account:
        st.warning(msg)
        st.session_state.post_login_goto = st.session_state.page  # lembra a página atual
        goto("auth")
        st.stop()

def post_login_redirect_if_needed():
    """Depois que o usuário faz login, volta para a página que ele estava."""
    dest = st.session_state.get("post_login_goto")
    if dest:
        del st.session_state["post_login_goto"]
        goto(dest)

def small_card(policy_row, met=None, missing=None, selectable=False, on_select=None):
    title = policy_row.get("Politicas publicas", "(sem título)")
    nivel = policy_row.get("nivel", "")
    with st.container(border=True):
        st.markdown(f"**{title}**  \n*Nível:* {nivel}")
        desc = policy_row.get("Descrição dos direitos", "")
        if desc:
            st.write(desc)
        acesso = policy_row.get("Acesso", "")
        if acesso:
            st.write("**Acesso (texto original):** ", acesso)
        link = policy_row.get("Link")
        if isinstance(link, str) and link.startswith("http"):
            st.markdown(f"[Abrir link]({link})")

        if met is not None or missing is not None:
            cols = st.columns(2)
            with cols[0]:
                if met:
                    st.write("✅ **Atendidos (mapeados):**")
                    for m in met:
                        st.write(f"- {m}")
            with cols[1]:
                if missing:
                    st.write("🟡 **Faltantes (mapeados):**")
                    for m in missing:
                        st.write(f"- {m}")

        if selectable and on_select:
            st.button("Quero escolher esta política", use_container_width=True, on_click=on_select)

def footer():
    st.markdown("---")
    logo_path = Path("static/logo.png")  # ajuste para .jpg/.svg se for o caso
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode()
        img_tag = f'<img src="data:image/png;base64,{b64}" width="140">'
    else:
        img_tag = "<!-- logo não encontrada -->"

    st.markdown(
        f"""
        <div style='text-align: center;'>
            <p><b>Desenvolvido por:</b> </p>
            <a href="https://redecaete.com" target="_blank">{img_tag}</a>
        </div>
        """,
        unsafe_allow_html=True
    )
# --- Helpers para o mapa do Observatório ---
def _guess_latlon_cols(df):
    """Retorna (lat_col, lon_col) se existirem no DF, senão (None, None)."""
    if df is None or df.empty:
        return None, None
    candidates = [
        ("lat", "lon"),
        ("latitude", "longitude"),
        ("lat", "long"),
        ("y", "x"),
    ]
    cols = {c.lower(): c for c in df.columns}
    for la, lo in candidates:
        if la in cols and lo in cols:
            return cols[la], cols[lo]
    return None, None

def _normalize_text(s):
    """Normaliza texto p/ comparação simples (minúsculo, sem espaços extras)."""
    if s is None:
        return ""
    return str(s).strip().lower()

# ------------------------------------------------------------------
# Páginas
# ------------------------------------------------------------------
def page_home():
    header_nav(
        "Matupiri - Plataforma de Busca de Políticas Públicas",
        "Na Amazônia, muita gente tem direito a benefícios e programas, mas nem sempre sabe como chegar até eles. "
        "O Marupiri nasceu pra mudar isso. Assim como o peixe marupiri que encontra seu caminho nos rios, a nossa "
        "plataforma ajuda comunidades a achar os caminhos certos dentro da burocracia."
    )

    with st.container(border=True):
        st.subheader("Bem-vindo!")
        st.write(
            "Esta ferramenta permite entender:\n"
            "- Quais políticas você pode acessar;\n"
            "- O que precisa ter ou fazer para acessar determinada política pública;\n"
            "- Ver quais políticas você já pode acessar e quais estão quase lá, com requisitos faltantes.\n\n"
            "O Marupiri é uma ferramenta de luta e cuidado com o nosso território. Ele foi feito para apoiar pescadores, "
            "agricultores, mulheres, jovens e todas as pessoas que vivem e fazem a Amazônia.\n\n"
            "O Caeté nasce do desejo coletivo de articular saberes, práticas e afetos em torno das ecologias costeiras da Amazônia. "
            "Atuamos como um centro de articulação entre comunidades, pesquisadores, artistas e movimentos sociais, "
            "promovendo ações de pesquisa, formação, comunicação e justiça socioambiental."
        )

    # 3 botões: explorar, cadastrar sem salvar, login/cadastro
    c1, c2, c3 = st.columns(3)
    with c1:
        st.button(
            "Conhecer políticas mapeadas",
            type="primary",
            use_container_width=True,
            on_click=lambda: goto("policies_overview"),
        )
    with c2:
        st.button(
            "Cadastrar perfil (sem salvar)",
            use_container_width=True,
            on_click=lambda: goto("profile"),  # permite sem login
        )
    with c3:
        st.button(
            "Entrar / Cadastrar",
            use_container_width=True,
            on_click=lambda: goto("auth"),
        )

    st.markdown("")  # respiro

    with st.container(border=True):
        st.subheader("Sobre o Caeté")
        st.markdown(
            """
            O **Caeté - Coletivo de Articulações Marginais** nasce do **desejo coletivo** de articular saberes, práticas e afetos em torno das **ecologias costeiras da Amazônia**.
            Atuamos *produzindo articulações* entre comunidades, pesquisadores(as), artistas e movimentos sociais, promovendo ações de
            **pesquisa, formação, comunicação e justiça socioambiental** em territórios que vivem **entre terra, água e encantamento**.
            """
        )
    
    st.markdown("")  # respiro

    with st.container(border=True):
        st.subheader("Observatório das Políticas")
        st.markdown(
            "Este espaço é destino a exibir os resultados"
        )
    
    st.button("📊 Abrir Observatório", use_container_width=True, on_click=lambda: goto("observatorio"))

    footer()

def page_auth():
    header_nav("Entrar ou criar conta", "Escolha o tipo de conta para continuar.")
    tab1, tab2 = st.tabs(["Pessoa Física", "Coletivo"])

    with tab1:
        st.subheader("Entrar (Pessoa Física)")
        u = st.text_input("Usuário", key="person_login_user")
        p = st.text_input("Senha", type="password", key="person_login_pass")
        if st.button("Entrar (PF)"):
            acc = authenticate_person(u, p)
            if acc:
                st.session_state.account = acc
                st.success(f"Bem-vindo(a), {acc.get('display_name') or acc.get('username')}!")
                post_login_redirect_if_needed()
                goto("home")
            else:
                st.error("Usuário/senha inválidos.")

        st.divider()
        st.subheader("Criar conta (Pessoa Física)")
        name = st.text_input("Nome")
        new_user = st.text_input("Usuário (login)")
        new_pass = st.text_input("Senha", type="password")
        if st.button("Criar conta (PF)"):
            try:
                _ = create_person_account(name, new_user, new_pass)
                st.success("Conta criada! Agora faça login.")
            except Exception as e:
                st.error(f"Erro ao criar conta PF: {e}")

    with tab2:
        st.subheader("Entrar (Coletivo)")
        cnpj = st.text_input("CNPJ (somente números)", key="coll_login_cnpj")
        p2 = st.text_input("Senha", type="password", key="coll_login_pass")
        if st.button("Entrar (Coletivo)"):
            acc = authenticate_collective(cnpj, p2)
            if acc:
                st.session_state.account = acc
                st.success("Bem-vind@, coletivo!")
                post_login_redirect_if_needed()
                goto("home")
            else:
                st.error("CNPJ/senha inválidos.")

        st.divider()
        st.subheader("Criar conta (Coletivo)")
        new_cnpj = st.text_input("CNPJ do coletivo")
        contact = st.text_input("Contato (e-mail/telefone)")
        new_pass2 = st.text_input("Senha do coletivo", type="password")
        if st.button("Criar conta (Coletivo)"):
            try:
                _ = create_collective_account(new_cnpj, contact, new_pass2)
                st.success("Conta criada! Agora faça login.")
            except Exception as e:
                st.error(f"Erro ao criar conta do coletivo: {e}")

    st.button("← Voltar à apresentação", on_click=lambda: goto("home"))

def page_policies_overview():
    header_nav("Políticas públicas mapeadas", "Explore as políticas e leia um breve resumo.")

    cols = st.columns([2, 2, 1])
    with cols[0]:
        q = st.text_input("Buscar por nome, direitos ou requisitos", value="").strip().lower()
    with cols[1]:
        niveis = sorted(df["nivel"].dropna().unique().tolist()) if "nivel" in df.columns else []
        sel_niveis = st.multiselect("Filtrar por nível", options=niveis, default=[])
    with cols[2]:
        limit = st.number_input("Qtd. itens", min_value=1, max_value=40, value=40, step=1)
        
        # Loga busca (se houver termo)
    if q:
        uf, mun = current_location_from_state()
        gender = current_gender_from_profile()
        try:
            log_event(kind="search", uf=uf, municipio=mun, query=q)
        except Exception:
            pass

    view = df.copy()
    if sel_niveis and "nivel" in view.columns:
        view = view[view["nivel"].isin(sel_niveis)]

    if q:
        def _contains(row):
            campos = []
            for c in ["Politicas publicas", "Descrição dos direitos", "Acesso", "Organização interna (Subprogramas e/ou Eixos)"]:
                if c in row and pd.notna(row[c]):
                    campos.append(str(row[c]).lower())
            return q in " | ".join(campos)
        view = view[view.apply(_contains, axis=1)]

    total = len(view)
    st.caption(f"Exibindo até {min(limit, total)} de {total} políticas encontradas.")

    if total == 0:
        st.info("Nenhuma política encontrada com os filtros atuais.")
    else:
        for _, row in view.head(int(limit)).iterrows():
            small_card(row, selectable=False)

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.button(
            "Conhecer políticas mapeadas",
            type="primary",
            use_container_width=True,
            on_click=lambda: goto("policies_overview"),
        )
    with c2:
        st.button(
            "Cadastrar perfil (sem salvar)",
            use_container_width=True,
            on_click=lambda: goto("profile"),
        )
    with c3:
        st.button(
            "Entrar / Cadastrar",
            use_container_width=True,
            on_click=lambda: goto("auth"),
        )

def page_profile():
    header_nav("Cadastro de perfil", "Preencha seu perfil para analisarmos a elegibilidade.")
    prev = st.session_state.profile
    profile = {}

    # === Sidebar: versões do dono logado (só se logado) ===
    if st.session_state.account:
        acc = st.session_state.account
        owner_id = acc["id"]
        with st.sidebar:
            st.header("Minhas versões de perfil")

            versoes = list(get_profiles_by_account(owner_id))  # (id, version, created_at, updated_at)

            def _fmt_label(row):
                ver     = row[1] if len(row) > 1 else "?"
                created = row[2] if len(row) > 2 else ""
                updated = row[3] if len(row) > 3 else created
                ts = updated or created or ""
                ts_show = ts[:16].replace("T", " ") if isinstance(ts, str) else ""
                return f"v{ver} • {ts_show}".strip()

            if versoes:
                if "vers_sel" in st.session_state and st.session_state["vers_sel"] not in versoes:
                    del st.session_state["vers_sel"]

                row_sel = st.selectbox("Selecionar versão", options=versoes, format_func=_fmt_label, key="vers_sel")

                pid, ver = None, None
                if row_sel:
                    try:
                        pid = row_sel[0]
                        ver = row_sel[1] if len(row_sel) > 1 else None
                    except Exception:
                        st.error(f"Formato inesperado: {row_sel}")

                colA, colB = st.columns(2)
                with colA:
                    if st.button("Carregar") and pid is not None:
                        st.session_state.current_profile_id = pid
                        st.session_state.profile = load_profile(pid)
                        st.success(f"Versão v{ver} carregada.")
                with colB:
                    if st.button("Usar como base") and pid is not None:
                        st.session_state.profile = load_profile(pid)
                        st.toast("Campos preenchidos a partir desta versão.")
            else:
                st.caption("Nenhuma versão salva ainda.")
    else:
        with st.sidebar:
            st.info("Entre ou crie conta para salvar e gerenciar versões do seu perfil.")

    # ===== UF fora do form (reativo) =====
    if not ufs_df.empty:
        uf_options = ufs_df["uf"].tolist()
        uf_labels  = {r["uf"]: r["uf_nome"] for _, r in ufs_df.iterrows()}

        def _on_change_uf():
            st.session_state.pop("municipio_select", None)
            st.session_state.pop("ibge_mun", None)

        default_uf = prev.get("estado") if prev.get("estado") in uf_options else (uf_options[0] if uf_options else "")
        st.selectbox(
            "Estado (UF)",
            options=uf_options,
            index=uf_options.index(default_uf) if default_uf in uf_options else 0,
            format_func=lambda x: f"{x} — {uf_labels.get(x, '')}",
            key="uf",
            on_change=_on_change_uf,
        )
    else:
        st.text_input("Estado (UF)", value=prev.get("estado", ""), key="uf")

    # ===== FORM =====
    with st.form("perfil_form"):
        sel_uf = st.session_state.get("uf", prev.get("estado", ""))

        # Município dependente da UF
        if not mun_df.empty:
            mun_opts_df = mun_df.loc[mun_df["uf"] == sel_uf, :] if sel_uf else mun_df
            nomes = mun_opts_df["nome_mun"].tolist()
            default_mun = prev.get("municipio") if prev.get("municipio") in nomes else (nomes[0] if nomes else "")
            st.selectbox(
                "Município",
                options=nomes,
                index=nomes.index(default_mun) if default_mun in nomes else 0,
                key="municipio_select",
            )
            if not mun_opts_df.empty and st.session_state.get("municipio_select"):
                row_sel = mun_opts_df.loc[mun_opts_df["nome_mun"] == st.session_state["municipio_select"]].head(1)
                if not row_sel.empty:
                    st.session_state["ibge_mun"] = str(row_sel.iloc[0]["ibge_mun"])
        else:
            st.text_input("Município", value=prev.get("municipio", ""), key="municipio_select")

        # Demais campos do schema
        for field, spec in schema.items():
            if field in ("estado", "municipio"):
                continue
            label = spec.get("label", field)
            t = spec.get("type", "text")
            if t == "text":
                profile[field] = st.text_input(label, value=prev.get(field, ""))
            elif t == "number":
                profile[field] = st.number_input(label, min_value=0.0, step=1.0, format="%.0f",
                                                 value=float(prev.get(field, 0) or 0))
            elif t == "bool":
                profile[field] = st.checkbox(label, value=bool(prev.get(field, False)))
            elif t == "select":
                options = spec.get("options", []) or [""]
                default = prev.get(field, options[0])
                profile[field] = st.selectbox(label, options, index=options.index(default) if default in options else 0)
            else:
                profile[field] = st.text_input(label, value=prev.get(field, ""))

        # === Botões de ação ===
        c1, c2, c3 = st.columns(3)
        submit_matches = c1.form_submit_button("Veja qual Política meu perfil atende", type="primary", use_container_width=True)
        submit_picker  = c2.form_submit_button("Quero escolher qual Política quero atender", use_container_width=True)
        submit_save    = c3.form_submit_button("💾 Salvar dados cadastrados", use_container_width=True)

    # Estado final do profile (fora do with form)
    profile["estado"] = st.session_state.get("uf", "")
    profile["municipio"] = st.session_state.get("municipio_select", "")
    if "ibge_mun" in st.session_state:
        profile["ibge_mun"] = st.session_state["ibge_mun"]

    # === Salvar dados vinculados à conta logada (pede login só aqui) ===
    if submit_save:
        require_login_for_save()
        owner_id = st.session_state.account["id"]
        if st.session_state.current_profile_id is None:
            pid = save_profile_for_account(owner_id, profile)
            st.session_state.current_profile_id = pid
            st.session_state.profile = profile
            st.success(f"Perfil salvo! (nova versão • id={pid})")
        else:
            try:
                update_profile_for_account(st.session_state.current_profile_id, owner_id, profile)
                st.session_state.profile = profile
                st.success(f"Perfil atualizado! (id={st.session_state.current_profile_id})")
            except PermissionError:
                st.error("Este perfil não pertence à sua conta.")

    # Botão 1 → calcular e ir para Resultados (matches)
    if submit_matches:
        st.session_state.profile = profile
        eligible_rows, nearly_rows = [], []
        if "Acesso" in df.columns:
            for idx, row in df.iterrows():
                acesso = row.get("Acesso", "")
                met, missing = evaluate_requirements(str(acesso), profile, kw_map)
                if acesso and (met or missing):
                    (nearly_rows if missing else eligible_rows).append((idx, met, missing))
        st.session_state.eligible = eligible_rows
        st.session_state.nearly = nearly_rows
        
        # Agrega requisitos PRESENTES (met) e AUSENTES (missing) dos "quase elegíveis"
        uf, mun = current_location_from_state()
        gender = current_gender_from_profile()
        missing_terms, met_terms = [], []
        for _, met, missing in nearly_rows:
            if missing: missing_terms.extend(list(missing))
            if met:     met_terms.extend(list(met))

        try:
            log_event(kind="matches", uf=uf, municipio=mun, gender=gender,
                      met=met_terms, missing=missing_terms,
                      extras={"eligible_cnt": len(eligible_rows), "nearly_cnt": len(nearly_rows)})
        except Exception:
            pass

        # NEW: políticas mais adequadas (#4) — gera um evento por política 100% elegível
        try:
            for idx, met, _missing in eligible_rows:
                pol = str(df.loc[idx].get("Politicas publicas", ""))
                log_event(kind="eligible", policy=pol, uf=uf, municipio=mun, gender=gender)
        except Exception:
            pass       

        goto("matches")

    # Botão 2 → ir para o seletor de política
    if submit_picker:
        st.session_state.profile = profile
        goto("policy_picker")

    st.button("← Voltar", use_container_width=True, on_click=lambda: goto("home"))

def page_observatorio():
    header_nav("Observatório", "Mapa de calor e rankings por localidade, gênero e período.")

    # --- Filtros (sidebar) ---
    with st.sidebar:
        st.header("Filtros")
        period = st.selectbox("Período", ["Últimos 7 dias", "Últimos 30 dias", "Últimos 90 dias", "Tudo"], index=1)
        uf_filter = st.text_input("UF (ex.: AM, PA, AC)", value="")
        mun_filter = st.text_input("Município (opcional)", value="")
        gender_filter = st.text_input("Gênero (opcional)", value="")  # ajuste para selectbox se tiver opções fixas
        topn = st.number_input("Top N (ranking)", min_value=3, max_value=50, value=10, step=1)

    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if period == "Últimos 7 dias":
        start_iso, end_iso = (now - timedelta(days=7)).isoformat(), now.isoformat()
    elif period == "Últimos 30 dias":
        start_iso, end_iso = (now - timedelta(days=30)).isoformat(), now.isoformat()
    elif period == "Últimos 90 dias":
        start_iso, end_iso = (now - timedelta(days=90)).isoformat(), now.isoformat()
    else:
        start_iso, end_iso = None, None

    uf_f  = uf_filter.strip().upper() or None
    mun_f = mun_filter.strip() or None
    gen_f = gender_filter.strip() or None

    # --- Busca eventos conforme filtros globais ---
    events = get_analytics(start_iso=start_iso, end_iso=end_iso, uf=uf_f, municipio=mun_f, gender=gen_f)
    if not events:
        st.info("Sem eventos para os filtros atuais.")
        return

    import pandas as pd
    ev = pd.DataFrame(events)

   # --- Seletor de Métrica (robusto) ---
  # --- Seletor de Métrica (robusto por índice) ---
    METRIC_OPTIONS = [
        ("req_by_gender", "1) Políticas Mais Requeridas (por Município/Região) por Gênero"),
        ("req_present",   "2) Requisitos Mais Presentes nas Buscas (por Município/Região)"),
        ("req_missing",   "3) Requisitos Mais Ausentes nas Buscas (por Município/Região)"),
        ("eligible",      "4) Políticas Mais Adequadas (por Município/Região)"),
        ("views",         "5) Políticas Mais Acessadas (por Município/Região)"),
    ]

    _default_index = min(4, len(METRIC_OPTIONS) - 1)  # garante índice válido mesmo se lista mudar

    choice_idx = st.selectbox(
        "Escolha a métrica",
        options=list(range(len(METRIC_OPTIONS))),
        index=_default_index,
        format_func=lambda i: METRIC_OPTIONS[i][1],
    )

    metric_code = METRIC_OPTIONS[choice_idx][0] #type: ignore

    # --- Geo helpers (para mapa) ---
    def _guess_latlon_cols(df):
        if df is None or df.empty: return None, None
        candidates = [("lat", "lon"), ("latitude", "longitude"), ("lat", "long"), ("y", "x")]
        cols = {c.lower(): c for c in df.columns}
        for la, lo in candidates:
            if la in cols and lo in cols:
                return cols[la], cols[lo]
        return None, None

    def _normalize_text(s):
        if s is None: return ""
        return str(s).strip().lower()

    lat_mun, lon_mun = _guess_latlon_cols(mun_df)
    lat_uf,  lon_uf  = _guess_latlon_cols(ufs_df)

    # --- Seleção do subconjunto por métrica + dados p/ ranking ---
    ranking_df = None
    heat_source = None
    heat_label  = "Eventos"

    if metric_code == "views":  # 5) Políticas Mais Acessadas
        view = ev[ev["kind"] == "view"].copy()
        if not view.empty:
            group_cols = (["uf", "municipio", "policy"] if (uf_f is None and mun_f is None) else ["policy"])
            ranking_df = (view.groupby(group_cols)
                            .size().reset_index(name="acessos")
                            .sort_values("acessos", ascending=False)
                            .head(int(topn)))
            heat_source = view.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Acessos"

    elif metric_code == "eligible":  # 4) Políticas mais adequadas
        elig = ev[ev["kind"] == "eligible"].copy()
        if not elig.empty:
            group_cols = (["uf", "municipio", "policy"] if (uf_f is None and mun_f is None) else ["policy"])
            ranking_df = (elig.groupby(group_cols)
                            .size().reset_index(name="adequações")
                            .sort_values("adequações", ascending=False)
                            .head(int(topn)))
            heat_source = elig.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Adequações"

    elif metric_code == "req_missing":  # 3) Requisitos mais ausentes
        mt = ev[(ev["kind"] == "matches") & ev["missing"].notna()].copy()
        if not mt.empty:
            expl = mt.explode("missing")
            group_cols = (["uf", "municipio", "missing"] if (uf_f is None and mun_f is None) else ["missing"])
            ranking_df = (expl.groupby(group_cols)
                            .size().reset_index(name="ocorrencias")
                            .sort_values("ocorrencias", ascending=False)
                            .head(int(topn)))
            heat_source = expl.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Ocorrências"

    elif metric_code == "req_present":  # 2) Requisitos mais presentes
        mt = ev[(ev["kind"] == "matches") & ev["met"].notna()].copy()
        if not mt.empty:
            expl = mt.explode("met")
            group_cols = (["uf", "municipio", "met"] if (uf_f is None and mun_f is None) else ["met"])
            ranking_df = (expl.groupby(group_cols)
                            .size().reset_index(name="ocorrencias")
                            .sort_values("ocorrencias", ascending=False)
                            .head(int(topn)))
            heat_source = expl.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Ocorrências"

    elif metric_code == "req_by_gender":  # 1) Requeridas por gênero (usamos 'view')
        vw = ev[ev["kind"] == "view"].copy()
        if not vw.empty:
            grp_cols = ["gender", "policy"]
            if uf_f is None and mun_f is None:
                grp_cols = ["uf", "municipio", "gender", "policy"]
            ranking_df = (vw.groupby(grp_cols)
                            .size().reset_index(name="requeridas")
                            .sort_values("requeridas", ascending=False)
                            .head(int(topn)))
            heat_source = vw.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Requisições"

    else:  # "1) Políticas Mais Requeridas ... por Gênero" -> usamos 'view' com corte de gênero
        vw = ev[ev["kind"] == "view"].copy()
        if not vw.empty:
            # ranking por gênero + política
            grp_cols = ["gender", "policy"]
            if uf_f is None and mun_f is None:
                grp_cols = ["uf", "municipio", "gender", "policy"]
            ranking_df = vw.groupby(grp_cols).size().reset_index(name="requeridas").sort_values("requeridas", ascending=False).head(int(topn))
            heat_source = vw.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Requisições"

    # --- Ranking (se houver) ---
    if ranking_df is not None and not ranking_df.empty:
        st.subheader("Ranking")
        st.dataframe(ranking_df, use_container_width=True)
    else:
        st.caption("Sem dados para o ranking com os filtros/métrica escolhidos.")

    # --- Mapa de Calor (abre sempre; se não houver heat_source na métrica, foca em 'search') ---
    base_for_map = heat_source
    defaulted_to_search = False
    if base_for_map is None or base_for_map.empty:
        # padrão: mostrar buscas
        srch = ev[ev["kind"] == "search"].copy()
        if not srch.empty:
            base_for_map = srch.groupby(["uf", "municipio"]).size().reset_index(name="weight")
            heat_label = "Buscas"
            defaulted_to_search = True

    if base_for_map is None or base_for_map.empty:
        st.info("Sem dados georreferenciados para desenhar o mapa de calor.")
        footer(); return

    # Preferimos coordenadas de município; senão, caímos para UF
    use_mun = lat_mun and lon_mun and ("nome_mun" in mun_df.columns)
    if use_mun:
        mun_aux = mun_df.copy()
        mun_aux["_key"] = mun_aux["nome_mun"].map(_normalize_text) + "||" + mun_aux["uf"].map(_normalize_text)
        base_for_map["_key"] = base_for_map["municipio"].map(_normalize_text) + "||" + base_for_map["uf"].map(_normalize_text)
        heat_df = base_for_map.merge(mun_aux[["_key", lat_mun, lon_mun, "nome_mun", "uf"]], on="_key", how="left").dropna(subset=[lat_mun, lon_mun])
        tooltip_cfg = {
            "html": f"<b>Município:</b> {{nome_mun}} {{uf}}<br/><b>{heat_label}:</b> {{weight}}",
            "style": {"backgroundColor": "rgba(30,30,30,0.9)", "color": "white"},
        }
        lon_col, lat_col = lon_mun, lat_mun
    else:
        if not (lat_uf and lon_uf and ("uf" in ufs_df.columns)):
            st.info("Não encontrei lat/lon em `municipios.csv` nem `ufs.csv`. Inclua colunas lat/lon para habilitar o mapa.")
            footer(); return
        ufs_aux = ufs_df.copy()
        ufs_aux["_key"] = ufs_aux["uf"].map(_normalize_text)
        base_for_map["_key"] = base_for_map["uf"].map(_normalize_text)
        heat_df = base_for_map.merge(ufs_aux[["_key", lat_uf, lon_uf, "uf"]], on="_key", how="left").dropna(subset=[lat_uf, lon_uf])
        tooltip_cfg = {
            "html": f"<b>UF:</b> {{uf}}<br/><b>{heat_label}:</b> {{weight}}",
            "style": {"backgroundColor": "rgba(30,30,30,0.9)", "color": "white"},
        }
        lon_col, lat_col = lon_uf, lat_uf

    # --- Heatmap (compatível com versões diferentes de pydeck) ---
    heat_layer = pdk.Layer(
        "HeatmapLayer",
        data=heat_df,
        get_position=f"[{lon_col}, {lat_col}]",
        get_weight="weight",
        aggregation="SUM",
        radiusPixels=40,
        intensity=1.0,
        threshold=0.03,
    )

    initial_view_state = pdk.ViewState(latitude=-14.2350, longitude=-51.9253, zoom=3.5, pitch=0)

    try:
        r = pdk.Deck(
            layers=[heat_layer],
            initial_view_state=initial_view_state,
            tooltip=tooltip_cfg,  # type: ignore
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        )
    except TypeError:
        r = pdk.Deck(
            layers=[heat_layer],
            initial_view_state=initial_view_state,
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        )

    # Título do mapa
    title_map = f"🗺️ Mapa de calor — {heat_label}"
    if defaulted_to_search:
        title_map += " (padrão: buscas)"
    st.subheader(title_map)
    st.pydeck_chart(r, use_container_width=True)

    footer()

def page_policy_picker():
    header_nav("Selecionar política", "Escolha uma política mapeada para verificar sua aderência ao seu perfil.")

    cols = st.columns([2, 2, 1])
    with cols[0]:
        q = st.text_input("Buscar por nome/direitos/requisitos", value="").strip().lower()
    with cols[1]:
        niveis = sorted(df["nivel"].dropna().unique().tolist()) if "nivel" in df.columns else []
        sel_niveis = st.multiselect("Filtrar por nível", options=niveis, default=[])
    with cols[2]:
        limit = st.number_input("Qtd. itens", min_value=1, max_value=50, value=20, step=1)

    view = df.copy()
    if sel_niveis and "nivel" in view.columns:
        view = view[view["nivel"].isin(sel_niveis)]
    if q:
        def _contains(row):
            campos = []
            for c in ["Politicas publicas", "Descrição dos direitos", "Acesso", "Organização interna (Subprogramas e/ou Eixos)"]:
                if c in row and pd.notna(row[c]):
                    campos.append(str(row[c]).lower())
            return q in " | ".join(campos)
        view = view[view.apply(_contains, axis=1)]

    nomes = view["Politicas publicas"].fillna("(sem título)").tolist() if "Politicas publicas" in view.columns else []
    if not nomes:
        st.info("Nenhuma política encontrado pelos filtros.")
        st.button("← Voltar ao cadastro", use_container_width=True, on_click=lambda: goto("profile"))
        return

    idx_map = {row["Politicas publicas"]: i for i, (_, row) in enumerate(view.iterrows()) if "Politicas publicas" in row}
    sel_nome = st.selectbox("Escolha a política", options=nomes, index=0)
    st.caption("Dica: filtre pelo nível ou use a busca para agilizar.")

    sel_row = view.iloc[idx_map.get(sel_nome, 0)]
    small_card(sel_row, selectable=False)

    def _pick_and_go():
        orig_idx = sel_row.name  # índice original do df
        st.session_state.selected_policy_idx = orig_idx

        # Loga view da política selecionada
        uf, mun = current_location_from_state()
        gender = current_gender_from_profile()
        try:
            log_event(kind="view",
                      policy=str(sel_row.get("Politicas publicas", "")),
                      uf=uf, municipio=mun, gender=gender)
        except Exception:
            pass

        goto("policy_detail")

    st.button("Analisar minha aderência a esta política", type="primary", use_container_width=True, on_click=_pick_and_go)

    st.divider()
    col_p1, col_p2 = st.columns([1, 2])
    with col_p1:
        if st.button("💾 Salvar informações (perfil + escolha)"):
            require_login_for_save("Faça login para salvar seu perfil e a política escolhida.")
            owner_id = st.session_state.account["id"]
            pid = save_profile_for_account(owner_id, st.session_state.profile)
            st.session_state.current_profile_id = pid
            st.success(f"Perfil salvo! (id={pid})")
    with col_p2:
        st.caption("Sem login você pode escolher e ver detalhes. Para **salvar**, entre ou crie conta.")

    c1, c2 = st.columns(2)
    with c1:
        st.button("← Voltar ao cadastro", use_container_width=True, on_click=lambda: goto("profile"))
    with c2:
        st.button("Voltar à apresentação", use_container_width=True, on_click=lambda: goto("home"))

def page_matches():
    header_nav("Políticas adequadas ao seu perfil", "Resultados com base no seu cadastro.")
    eligible_rows = st.session_state.get("eligible", [])
    nearly_rows = st.session_state.get("nearly", [])

    st.markdown("### ✅ Elegíveis")
    if eligible_rows:
        for idx, met, missing in eligible_rows:
            r = df.loc[idx]
            def on_select(idx=idx):
                st.session_state.selected_policy_idx = idx
                goto("policy_detail")
            small_card(r, met=met, missing=missing, selectable=True, on_select=on_select)
    else:
        st.info("Nenhuma política 100% elegível encontrada com as regras atuais.")

    st.markdown("### 🟡 Quase elegíveis (o que falta)")
    if nearly_rows:
        for idx, met, missing in nearly_rows:
            r = df.loc[idx]
            def on_select(idx=idx):
                st.session_state.selected_policy_idx = idx
                goto("policy_detail")
            small_card(r, met=met, missing=missing, selectable=True, on_select=on_select)
    else:
        st.info("Nenhuma política parcialmente elegível encontrada com as regras atuais.")

    cols = st.columns(2)
    with cols[0]:
        st.button("← Editar perfil", use_container_width=True, on_click=lambda: goto("profile"))
    with cols[1]:
        st.button("Voltar à apresentação", use_container_width=True, on_click=lambda: goto("home"))

    st.divider()
    col_s1, col_s2 = st.columns([1, 2])
    with col_s1:
        if st.button("💾 Salvar informações"):
            require_login_for_save("Faça login para salvar seus resultados (e o perfil).")
            owner_id = st.session_state.account["id"]
            pid = save_profile_for_account(owner_id, st.session_state.profile)
            st.session_state.current_profile_id = pid
            st.success(f"Perfil salvo! (id={pid})")
    with col_s2:
        st.caption("Você pode explorar sem login. Para **salvar** seu perfil e resultados, entre ou crie uma conta.")

    footer()

def page_policy_detail():
    header_nav("Detalhe da política selecionada", "Veja os requisitos que você já atende e o que falta.")
    sel_idx = st.session_state.get("selected_policy_idx")
    if sel_idx is None or sel_idx not in df.index:
        st.warning("Nenhuma política selecionada.")
        st.button("← Voltar aos resultados", on_click=lambda: goto("matches"))
        return

    row = df.loc[sel_idx]
    acesso = row.get("Acesso", "")
    met, missing = evaluate_requirements(str(acesso), st.session_state.profile, kw_map)

    small_card(row, met=met, missing=missing, selectable=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.button("← Resultados", use_container_width=True, on_click=lambda: goto("matches"))
    with c2:
        st.button("Editar perfil", use_container_width=True, on_click=lambda: goto("profile"))
    with c3:
        st.button("Voltar à apresentação", use_container_width=True, on_click=lambda: goto("home"))

    footer()

# ------------------------------------------------------------------
# Router
# ------------------------------------------------------------------
page = st.session_state.page
if page == "home":
    page_home()
elif page == "policies_overview":
    page_policies_overview()
elif page == "profile":
    page_profile()
elif page == "matches":
    page_matches()
elif page == "policy_detail":
    page_policy_detail()
elif page == "policy_picker":
    page_policy_picker()
elif page == "auth":
    page_auth()
elif page == "observatorio":
    page_observatorio()
else:
    page_home()