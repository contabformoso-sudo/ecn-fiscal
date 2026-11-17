import streamlit as st
import os
import random
import requests
import json
import base64
import time
import pandas as pd
import io
import pdfplumber
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

st.set_page_config(page_title="PDV ECN - Emissão de NFC-e", page_icon="🧾", layout="wide")

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# --- CONTROLE DE LOGIN ---
if 'cliente_logado' not in st.session_state:
    st.session_state.cliente_logado = None

if st.session_state.cliente_logado is None:
    st.title("🔐 Acesso ao Sistema")
    st.write("Digite o CNPJ da sua loja e a senha para emitir notas.")
    
    with st.form("login_form"):
        cnpj_input = st.text_input("CNPJ (apenas números)", max_chars=14)
        senha_input = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar")
        
        if submit:
            res = supabase.table('clientes').select('*').eq('cnpj', cnpj_input).eq('senha', senha_input).execute()
            if res.data:
                st.session_state.cliente_logado = res.data[0]
                st.rerun()
            else:
                st.error("CNPJ ou senha inválidos.")
    st.stop()

# Se chegou aqui, está logado!
cliente = st.session_state.cliente_logado
TOKEN_CLIENTE = cliente.get('token_focus_homologacao', '').strip()
CNPJ_CLIENTE = cliente['cnpj']
ID_CLIENTE = cliente['id']
auth_encoded = base64.b64encode(f"{TOKEN_CLIENTE}:".encode()).decode()

# --- FUNÇÕES ---
@st.cache_data(ttl=60)
def buscar_produtos_com_estoque(id_cliente):
    # AGORA BUSCA A UNIDADE TAMBÉM
    produtos = supabase.table('produtos').select('id, descricao, preco_venda, ncm, ean, sujeito_st, unidade').eq('cliente_id', id_cliente).execute()
    produtos_com_estoque = []
    for p in produtos.data:
        if p.get('preco_venda') is None or p['preco_venda'] <= 0: continue
        movs = supabase.table('estoque_movimentacoes').select('tipo_movimento, quantidade').eq('produto_id', p['id']).execute()
        saldo = sum(float(m['quantidade']) if m['tipo_movimento'] == 'ENTRADA' else -float(m['quantidade']) for m in movs.data)
        if saldo > 0:
            produtos_com_estoque.append({
                'id': p['id'], 'descricao': p['descricao'], 'preco': float(p['preco_venda']),
                'saldo': saldo, 'ncm': p.get('ncm') or '61091000', 'ean': p.get('ean') or 'SEM GTIN',
                'sujeito_st': p.get('sujeito_st', False),
                'unidade': p.get('unidade') or 'UN' # <-- NOVO CAMPO
            })
    return produtos_com_estoque

def emitir_nota_focus(carrinho, valor_desconto, valor_total, cnpj_emitente, token_focus):
    fuso_brasilia = timezone(timedelta(hours=-3))
    data_agora = datetime.now(fuso_brasilia).strftime("%Y-%m-%dT%H:%M:%S-03:00")
    items_json = []
    for index, item in enumerate(carrinho):
        cfop_saida = "5405" if item.get('sujeito_st') else "5102"
        csosn_saida = "500" if item.get('sujeito_st') else "102"
        item_json = {
            "numero_item": str(index + 1), "codigo_produto": str(item['id'])[:10], 
            "descricao": item['descricao'], "cfop": cfop_saida, 
            "unidade_comercial": item.get('unidade', 'UN'), # <-- USA A UNIDADE DO PRODUTO
            "quantidade_comercial": float(item['quantidade']), "valor_unitario_comercial": float(item['preco_unitario']),
            "valor_bruto": float(item['subtotal']), "codigo_ncm": item['ncm'], "codigo_ean": item['ean'],
            "icms_situacao_tributaria": csosn_saida, "icms_origem": "0",
            "pis_situacao_tributaria": "07", "cofins_situacao_tributaria": "07"
        }
        if csosn_saida == "500":
            item_json["valor_icms_st"] = 0.00
            item_json["valor_bc_icms_st"] = 0.00
        items_json.append(item_json)

    payload = {
        "natureza_operacao": "Venda ao Consumidor", "data_emissao": data_agora, "tipo_documento": 1,
        "finalidade_emissao": 1, "consumidor_final": 1, "presenca_comprador": 1, "modalidade_frete": "9", 
        "cnpj_emitente": cnpj_emitente, "destinatario": {"cpf": "12345678909", "nome": "CONSUMIDOR TESTE"},
        "items": items_json, "desconto": float(valor_desconto),
        "pagamentos": [{"forma_pagamento": "01", "valor_pagamento": float(valor_total)}]
    }
    headers = {"Authorization": f"Basic {auth_encoded}", "Content-Type": "application/json"}
    ref_nota = f"pdv_{int(time.time())}_{random.randint(1,999)}" 
    url_focus = f"https://homologacao.focusnfe.com.br/v2/nfce?ref={ref_nota}"
    try:
        response = requests.post(url_focus, json=payload, headers=headers)
        return response.status_code, response.json()
    except Exception as e:
        return 500, {"erro": str(e)}

def montar_carrinho_rapido(valor_alvo, produtos):
    random.shuffle(produtos)
    valor_teto = valor_alvo * 1.10
    carrinho = []
    valor_atual = 0.0
    for p in produtos:
        if valor_atual >= valor_alvo: break
        qtd_necessaria = int((valor_alvo - valor_atual) // p['preco'])
        qtd_necessaria = max(1, qtd_necessaria)
        qtd_necessaria = min(qtd_necessaria, int(p['saldo']))
        subtotal = qtd_necessaria * p['preco']
        while valor_atual + subtotal > valor_teto and qtd_necessaria > 1:
            qtd_necessaria -= 1
            subtotal = qtd_necessaria * p['preco']
        if valor_atual + subtotal <= valor_teto:
            carrinho.append({
                'id': p['id'], 'descricao': p['descricao'], 'quantidade': qtd_necessaria,
                'preco_unitario': p['preco'], 'subtotal': subtotal, 'ncm': p['ncm'],
                'ean': p['ean'], 'sujeito_st': p['sujeito_st'], 'unidade': p['unidade']
            })
            valor_atual += subtotal
    if valor_atual > 0:
        return {'itens': carrinho, 'valor_atual': valor_atual, 'diferenca': valor_atual - valor_alvo, 'valor_alvo': valor_alvo}
    return None

def calcular_faturamento_mes(id_cliente):
    hoje = datetime.now()
    primeiro_dia = hoje.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    notas = supabase.table('notas_emitidas').select('valor_total').eq('cliente_id', id_cliente).gte('emitida_em', primeiro_dia).execute()
    total = sum(float(n['valor_total']) for n in notas.data) if notas.data else 0.0
    return total, len(notas.data)

# --- BARRA LATERAL ---
with st.sidebar:
    st.header(f"Olá, {cliente['nome']}")
    if st.button("Sair (Logout)"):
        st.session_state.cliente_logado = None
        st.rerun()
    st.divider()
    st.subheader("❓ Ajuda / Manual")
    with st.expander("Como emitir uma venda detalhada?"):
        st.write("1. Busque o produto.\n2. Digite a quantidade e clique em Adicionar.\n3. Repita se necessário.\n4. Clique em Emitir NFC-e.")
    with st.expander("Como emitir uma venda rápida?"):
        st.write("1. Digite o valor total pago.\n2. O sistema monta o carrinho.\n3. Confirme a emissão.")
    with st.expander("Onde pego o PDF da Nota?"):
        st.write("Na aba Histórico de Notas, clique em Baixar DANFE.")

# --- INTERFACE PRINCIPAL ---
st.title("🛒 PDV Rápido")

faturamento, qtd_notas = calcular_faturamento_mes(ID_CLIENTE)
col1, col2 = st.columns(2)
col1.metric("Faturamento no Mês", f"R$ {faturamento:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
col2.metric("Notas Emitidas", qtd_notas)

aba_detalhada, aba_rapida, aba_csv, aba_historico = st.tabs([
    "🛍️ Venda Detalhada (Carrinho)", 
    "⚡ Venda Rápida (Valor)", 
    "📥 Fechamento (CSV)", 
    "🧾 Histórico"
])

# --- ABA 1: VENDA DETALHADA ---
with aba_detalhada:
    st.subheader("Adicione os itens um por um")
    
    if 'carrinho_manual' not in st.session_state:
        st.session_state.carrinho_manual = []

    produtos_db = buscar_produtos_com_estoque(ID_CLIENTE)
    
    if produtos_db:
        termo_busca = st.text_input("🔎 Buscar produto por nome ou código:", placeholder="Ex: cimento, pijama...")
        
        produtos_filtrados = [p for p in produtos_db if termo_busca.lower() in p['descricao'].lower() or termo_busca in (p['ean'] or '')]
        
        if produtos_filtrados:
            for p in produtos_filtrados:
                with st.container():
                    col_nome, col_qtd, col_btn = st.columns([3, 1, 1])
                    col_nome.write(f"**{p['descricao']}** (Estoque: {p['saldo']:g} {p['unidade']}) - R$ {p['preco']:.2f}")
                    qtd = col_qtd.number_input("Qtd", min_value=1.0, max_value=float(p['saldo']), step=1.0, key=f"qtd_{p['id']}", label_visibility="collapsed")
                    if col_btn.button("➕ Adicionar", key=f"btn_{p['id']}"):
                        item_existente = next((item for item in st.session_state.carrinho_manual if item['id'] == p['id']), None)
                        if item_existente:
                            item_existente['quantidade'] += qtd
                            item_existente['subtotal'] = item_existente['quantidade'] * item_existente['preco_unitario']
                        else:
                            st.session_state.carrinho_manual.append({
                                'id': p['id'], 'descricao': p['descricao'], 'quantidade': qtd,
                                'preco_unitario': p['preco'], 'subtotal': qtd * p['preco'], 
                                'ncm': p['ncm'], 'ean': p['ean'], 'sujeito_st': p['sujeito_st'], 'unidade': p['unidade']
                            })
                        st.rerun()
        else:
            st.info("Nenhum produto encontrado com essa busca.")

    st.divider()
    st.subheader("🛒 Carrinho Atual")
    
    if st.session_state.carrinho_manual:
        total_carrinho = 0.0
        
        for i, item in enumerate(st.session_state.carrinho_manual):
            col_desc, col_preco, col_subtotal, col_rem = st.columns([3, 1.5, 1.5, 0.5])
            
            col_desc.write(f"**{item['quantidade']:g} {item['unidade']}** {item['descricao']}")
            
            novo_preco = col_preco.number_input(
                "Preço Unit. (R$)", 
                value=float(item['preco_unitario']), 
                step=1.0, 
                key=f"preco_carrinho_{item['id']}",
                label_visibility="collapsed"
            )
            
            if novo_preco != item['preco_unitario']:
                st.session_state.carrinho_manual[i]['preco_unitario'] = novo_preco
                st.session_state.carrinho_manual[i]['subtotal'] = item['quantidade'] * novo_preco
                st.rerun()
                
            col_subtotal.write(f"R$ {item['subtotal']:.2f}")
            
            if col_rem.button("❌", key=f"rem_{i}"):
                st.session_state.carrinho_manual.pop(i)
                st.rerun()
            
            total_carrinho += item['subtotal']
        
        st.write(f"### Total: R$ {total_carrinho:.2f}")
        
        if st.button("🚀 Emitir NFC-e do Carrinho", type="primary", use_container_width=True):
            with st.spinner("📡 Emitindo..."):
                status_code, retorno = emitir_nota_focus(st.session_state.carrinho_manual, 0.0, total_carrinho, CNPJ_CLIENTE, TOKEN_CLIENTE)
                if status_code in [200, 201, 202] and retorno.get('status') == 'autorizado':
                    supabase.table('notas_emitidas').insert({
                        'cliente_id': ID_CLIENTE, 'cliente_cnpj': CNPJ_CLIENTE, 'numero_nota': retorno.get('numero'),
                        'serie': retorno.get('serie'), 'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                        'status': retorno.get('status'), 'valor_total': total_carrinho, 'json_retorno': retorno
                    }).execute()
                    for item in st.session_state.carrinho_manual:
                        supabase.table('estoque_movimentacoes').insert({
                            'cliente_id': ID_CLIENTE, 'produto_id': item['id'], 'tipo_movimento': 'SAIDA',
                            'quantidade': item['quantidade'], 'valor_unitario': item['preco_unitario'], 'origem': 'VENDA_DETALHADA'
                        }).execute()
                    st.balloons()
                    st.success("🎉 NOTA AUTORIZADA E ESTOQUE ATUALIZADO!")
                    st.session_state.carrinho_manual = []
                    st.rerun()
                else:
                    st.error(f"Erro: {retorno.get('mensagem_sefaz')}")
    else:
        st.info("O carrinho está vazio.")

# --- ABA 2: VENDA RÁPIDA ---
with aba_rapida:
    st.write("Digite o valor total da venda e o sistema monta o carrinho automaticamente.")
    valor_alvo = st.number_input("Valor Total da Venda (R$):", min_value=1.0, value=50.0, step=10.0)
    
    if 'carrinho_atual' not in st.session_state: 
        st.session_state.carrinho_atual = None
        
    if st.button("🔍 Montar Carrinho Automático", type="primary"):
        st.session_state.carrinho_atual = None
        produtos = buscar_produtos_com_estoque(ID_CLIENTE)
        if produtos:
            carrinho = montar_carrinho_rapido(valor_alvo, produtos)
            if carrinho: st.session_state.carrinho_atual = carrinho
            else: st.error("❌ Não foi possível montar o carrinho com esse valor.")
        else: st.error("Não há produtos com estoque.")
        
    if st.session_state.carrinho_atual:
        dados = st.session_state.carrinho_atual
        st.success(f"✅ Carrinho montado! Desconto: R$ {dados['diferenca']:.2f}")
        for item in dados['itens']:
            st.write(f"📦 **{item['quantidade']:g} {item['unidade']}** {item['descricao']} - R$ {item['preco_unitario']:.2f}")
        st.write(f"### 🎯 Total a Pagar: R$ {dados['valor_alvo']:.2f}")
        
        if st.button("🚀 Confirmar e Emitir NFC-e", type="primary", key="btn_emitir_manual"):
            with st.spinner("📡 Emitindo..."):
                status_code, retorno = emitir_nota_focus(dados['itens'], dados['diferenca'], dados['valor_alvo'], CNPJ_CLIENTE, TOKEN_CLIENTE)
                if status_code in [200, 201, 202] and retorno.get('status') == 'autorizado':
                    supabase.table('notas_emitidas').insert({
                        'cliente_id': ID_CLIENTE, 'cliente_cnpj': CNPJ_CLIENTE, 'numero_nota': retorno.get('numero'),
                        'serie': retorno.get('serie'), 'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                        'status': retorno.get('status'), 'valor_total': dados['valor_alvo'], 'json_retorno': retorno
                    }).execute()
                    for item in dados['itens']:
                        supabase.table('estoque_movimentacoes').insert({
                            'cliente_id': ID_CLIENTE, 'produto_id': item['id'], 'tipo_movimento': 'SAIDA',
                            'quantidade': item['quantidade'], 'valor_unitario': item['preco_unitario'], 'origem': 'VENDA_PAINEL'
                        }).execute()
                    st.balloons()
                    st.success("🎉 NOTA AUTORIZADA E ESTOQUE ATUALIZADO!")
                    st.session_state.carrinho_atual = None
                    st.rerun()
                else:
                    st.error(f"Erro: {retorno.get('mensagem_sefaz')}")

# --- ABA 3: FECHAMENTO DE CAIXA (CSV) ---
with aba_csv:
    st.write("Importe o relatório da sua maquininha (formato CSV) para emitir as notas do dia.")
    arquivo_csv = st.file_uploader("Selecione o arquivo CSV", type="csv")
    df_vendas = None
    
    if arquivo_csv is not None:
        try:
            df = pd.read_csv(arquivo_csv, encoding='utf-8')
            coluna_valor = 'Valor (R$)' if 'Valor (R$)' in df.columns else ('Valor' if 'Valor' in df.columns else None)
            coluna_status = 'Status' if 'Status' in df.columns else None
            
            if coluna_valor:
                df_vendas = df.copy()
                if coluna_status: df_vendas = df_vendas[df_vendas[coluna_status] == 'Aprovada']
                df_vendas['Valor'] = df_vendas[coluna_valor].astype(str).str.replace('.', '').str.replace(',', '.').astype(float)
            else:
                st.error("O arquivo enviado não tem a coluna 'Valor' ou 'Valor (R$)'.")
        except Exception as e:
            st.error(f"Erro ao ler o arquivo: {e}")

    if df_vendas is not None and not df_vendas.empty:
        st.success(f"📊 Arquivo lido com sucesso! Encontradas **{len(df_vendas)}** vendas.")
        st.write(f"💰 Valor total: **R$ {df_vendas['Valor'].sum():.2f}**")
        st.divider()
        
        if st.button("🚀 EMITIR TODAS AS NOTAS AGORA", type="primary", use_container_width=True):
            produtos = buscar_produtos_com_estoque(ID_CLIENTE)
            if not produtos: st.error("Não há estoque para emitir.")
            else:
                sucessos, erros = 0, 0
                progresso = st.progress(0)
                status_texto = st.empty()
                vendas_para_emitir = df_vendas.head(15)
                
                for index, row in vendas_para_emitir.iterrows():
                    valor_venda = float(row['Valor'])
                    status_texto.write(f"⏳ Emitindo nota {index+1} de {len(vendas_para_emitir)} (R$ {valor_venda:.2f})...")
                    carrinho = montar_carrinho_rapido(valor_venda, produtos)
                    if carrinho:
                        status_code, retorno = emitir_nota_focus(carrinho['itens'], carrinho['diferenca'], valor_venda, CNPJ_CLIENTE, TOKEN_CLIENTE)
                        if status_code in [200, 201, 202] and retorno.get('status') == 'autorizado':
                            supabase.table('notas_emitidas').insert({
                                'cliente_id': ID_CLIENTE, 'cliente_cnpj': CNPJ_CLIENTE, 'numero_nota': retorno.get('numero'),
                                'serie': retorno.get('serie'), 'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                                'status': retorno.get('status'), 'valor_total': valor_venda, 'json_retorno': retorno
                            }).execute()
                            for item in carrinho['itens']:
                                supabase.table('estoque_movimentacoes').insert({
                                    'cliente_id': ID_CLIENTE, 'produto_id': item['id'], 'tipo_movimento': 'SAIDA',
                                    'quantidade': item['quantidade'], 'valor_unitario': item['preco_unitario'], 'origem': 'VENDA_CSV'
                                }).execute()
                            sucessos += 1
                        else: erros += 1
                    else: erros += 1
                    progresso.progress((index + 1) / len(vendas_para_emitir))
                    time.sleep(2)
                status_texto.empty()
                st.success(f"🏁 Finalizado! Sucessos: {sucessos} | Erros: {erros}")
                st.rerun()

# --- ABA 4: HISTÓRICO ---
with aba_historico:
    st.header("🧾 Histórico de Notas Emitidas")
    notas = supabase.table('notas_emitidas').select('*').eq('cliente_id', ID_CLIENTE).order('emitida_em', desc=True).limit(50).execute()
    if notas.data:
        for n in notas.data:
            data_formatada = "Data não disponível"
            if n.get('emitida_em'):
                dt = datetime.fromisoformat(n['emitida_em'].replace('Z', '+00:00'))
                data_formatada = dt.strftime('%d/%m/%Y %H:%M')
            with st.expander(f"📄 Nota {n.get('numero_nota', 'S/N')} - R$ {n.get('valor_total', 0):.2f} ({data_formatada})"):
                st.write(f"**Status:** {n.get('status', 'N/A')}")
                st.write(f"**Chave de Acesso:** `{n.get('chave_acesso', 'N/A')}`")
                json_retorno = n.get('json_retorno', {})
                caminho_danfe = json_retorno.get('caminho_danfe')
                if caminho_danfe:
                    url_danfe = f"https://homologacao.focusnfe.com.br{caminho_danfe}"
                    st.markdown(f"[📥 Clique aqui para baixar o DANFE (PDF)]({url_danfe})")
                else:
                    st.warning("Link do PDF não disponível para esta nota.")
    else:
        st.info("Nenhuma nota emitida ainda.")
