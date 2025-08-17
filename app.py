import streamlit as st
import pandas as pd
import os
from utils import evaluate_requirements, load_keyword_map
import base64
import base64
from pathlib import Path

# ----------------------
# Configuração geral
# ----------------------
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
    # Ordene/filtre as colunas mais importantes se existirem:
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
    import json
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    kw_map = load_keyword_map(KW_PATH)
    return schema, kw_map

df = load_data()
schema, kw_map = load_configs()

# ----------------------
# Estado de navegação
# ----------------------
if "page" not in st.session_state:
    st.session_state.page = "home"  # home, policies_overview, profile, matches, policy_detail

if "profile" not in st.session_state:
    st.session_state.profile = {}

if "eligible" not in st.session_state:
    st.session_state.eligible = []  # lista de tuples (idx, met, missing)

if "nearly" not in st.session_state:
    st.session_state.nearly = []    # lista de tuples (idx, met, missing)

if "selected_policy_idx" not in st.session_state:
    st.session_state.selected_policy_idx = None

def goto(page_name: str):
    st.session_state.page = page_name

# ----------------------
# Componentes de UI
# ----------------------
def header_nav(title: str, subtitle: str = ""):
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()

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
        # Troque image/png por image/jpeg se sua logo for .jpg
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
# ----------------------
# Páginas
# ----------------------
def page_home():
    header_nav("Plataforma Mapinguari",
               "Prototipo para indicar políticas alinhadas ao seu perfil e mostrar o que falta para acessar outras.")

    st.subheader("Bem-vindo!")
    st.write(
        "Esta ferramenta permite:\n"
        "- Conhecer as políticas públicas mapeadas;\n"
        "- Cadastrar seu perfil (atividade, renda, documentos, etc.);\n"
        "- Ver quais políticas você já pode acessar e quais estão quase lá, com requisitos faltantes."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.button("Conhecer políticas mapeadas", type="primary", use_container_width=True,
                  on_click=lambda: goto("policies_overview"))
    with c2:
        st.button("Cadastrar perfil", use_container_width=True, on_click=lambda: goto("profile"))
    footer()

def page_policies_overview():
    header_nav("Políticas públicas mapeadas", "Resumo das políticas presentes na base.")
    # Mostra uma tabela leve com colunas principais
    cols = [c for c in ["Politicas publicas", "nivel", "Descrição dos direitos", "Acesso", "Link"] if c in df.columns]
    st.dataframe(df[cols], use_container_width=True, height=460)
    st.divider()
    st.button("Cadastrar perfil", type="primary", use_container_width=True, on_click=lambda: goto("profile"))
    st.button("← Voltar à apresentação", use_container_width=True, on_click=lambda: goto("home"))
    footer()

def page_profile():
    header_nav("Cadastro de perfil", "Preencha seu perfil para analisarmos a elegibilidade.")
    profile = {}
    with st.form("perfil_form"):
        for field, spec in schema.items():
            label = spec.get("label", field)
            t = spec.get("type", "text")
            if t == "text":
                profile[field] = st.text_input(label, value=st.session_state.profile.get(field, ""))
            elif t == "number":
                profile[field] = st.number_input(label, min_value=0.0, step=1.0, format="%.0f",
                                                 value=float(st.session_state.profile.get(field, 0) or 0))
            elif t == "bool":
                profile[field] = st.checkbox(label, value=bool(st.session_state.profile.get(field, False)))
            elif t == "select":
                options = spec.get("options", [])
                # Garantir uma opção vazia caso não definido:
                options = options if options else [""]
                # Seleção com default no estado se possível:
                default = st.session_state.profile.get(field, options[0])
                profile[field] = st.selectbox(label, options, index=options.index(default) if default in options else 0)
            else:
                profile[field] = st.text_input(label, value=st.session_state.profile.get(field, ""))

        submitted = st.form_submit_button("Ver políticas adequadas", type="primary", use_container_width=True)

    if submitted:
        st.session_state.profile = profile
        # Calcula elegíveis e quase para ir à página de resultados
        eligible_rows = []
        nearly_rows = []
        if "Acesso" in df.columns:
            for idx, row in df.iterrows():
                acesso = row.get("Acesso", "")
                met, missing = evaluate_requirements(str(acesso), profile, kw_map)
                if acesso and (met or missing):
                    if missing:
                        nearly_rows.append((idx, met, missing))
                    else:
                        eligible_rows.append((idx, met, missing))
        st.session_state.eligible = eligible_rows
        st.session_state.nearly = nearly_rows
        goto("matches")

    st.button("← Voltar", use_container_width=True, on_click=lambda: goto("home"))
    footer()

def page_matches():
    header_nav("Políticas adequadas ao seu perfil", "Resultados com base no seu cadastro.")
    eligible_rows = st.session_state.get("eligible", [])
    nearly_rows = st.session_state.get("nearly", [])

    # Elegíveis 100%
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
    footer()

def page_policy_detail():
    header_nav("Detalhe da política selecionada",
               "Veja os requisitos que você já atende e o que falta.")
    sel_idx = st.session_state.get("selected_policy_idx")
    if sel_idx is None or sel_idx not in df.index:
        st.warning("Nenhuma política selecionada.")
        st.button("← Voltar aos resultados", on_click=lambda: goto("matches"))
        return

    row = df.loc[sel_idx]
    # Recalcular met/missing com o perfil atual (garante consistência se o usuário mudou o perfil)
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

# ----------------------
# Router
# ----------------------
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
else:
    page_home()