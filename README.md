Recomendador de Políticas Públicas (protótipo)

Como rodar no VS Code:
1) (Opcional) Crie um ambiente virtual: python -m venv .venv e ative.
2) Instale dependências: pip install -r requirements.txt
3) Rode: streamlit run app.py

Edite:
- profile_schema.json para alterar campos do cadastro.
- keyword_map.json para mapear palavras-chave de requisitos para verificações no perfil.

Funcionamento:
- O app lê a coluna 'Acesso' do Excel (data/politicas_publicas.xlsx).
- Para cada palavra-chave detectada, aplica a regra correspondente no perfil.
- Mostra políticas elegíveis e quase elegíveis com requisitos faltantes.
