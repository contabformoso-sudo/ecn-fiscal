import streamlit as st
import os
import random
import requests
import json
import base64
import time
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

st.set_page_config(page_title="PDV ECN - Emissão de NFC-e", page_icon="🧾", layout="centered")

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
token_focus = os.environ.get("FOCUS_NFE_TOKEN", "").strip()
auth_encoded = base64.b64encode(f"{token_focus}:".encode()).decode()

supabase: Client = create_client(url, key)

TOLERANCIA_TETO = 0.10
TOLERANCIA_DESCONTO = 0.10

@st.cache_data(ttl=60)
def buscar_produtos_com_estoque():
    produtos = supabase.table('produtos').select('id, descricao, preco_venda, ncm, ean, sujeito_st').execute()
    produtos_com_estoque = []
    
    for p in produtos.data:
        if p.get('preco_venda') is None or p['preco_venda'] <= 0: continue
            
        movs = supabase.table('estoque_movimentacoes').select('tipo_movimento, quantidade').eq('produto_id', p['id']).execute()
        saldo = sum(float(m['quantidade']) if m['tipo_movimento'] == 'ENTRADA' else -float(m['quantidade']) for m in movs.data)
                
        if saldo > 0:
            produtos_com_estoque.append({
                'id': p['id'], 'descricao': p['descricao'], 'preco': float(p['preco_venda']),
                'saldo': saldo, 'ncm': p.get('ncm') or '61091000', 'ean': p.get('ean') or 'SEM GTIN',
                'sujeito_st': p.get('sujeito_st', False)
            })
    return produtos_com_estoque

def emitir_nota_focus(carrinho, valor_desconto, valor_total):
    fuso_brasilia = timezone(timedelta(hours=-3))
    data_agora = datetime.now(fuso_brasilia).strftime("%Y-%m-%dT%H:%M:%S-03:00")
    
    items_json = []
    for index, item in enumerate(carrinho):
        cfop_saida = "5405" if item.get('sujeito_st') else "5102"
        csosn_saida = "500" if item.get('sujeito_st') else "102"

        item_json = {
            "numero_item": str(index + 1), "codigo_produto": str(item['id'])[:10], 
            "descricao": item['descricao'], "cfop": cfop_saida, "unidade_comercial": "UN",
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
        "cnpj_emitente": "57468706000108", "destinatario": {"cpf": "12345678909", "nome": "CONSUMIDOR TESTE"},
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

def montar_carrinho(valor_alvo, produtos):
    random.shuffle(produtos)
    valor_teto = valor_alvo * (1 + TOLERANCIA_TETO)
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
                'ean': p['ean'], 'sujeito_st': p['sujeito_st']
            })
            valor_atual += subtotal

    diferenca = valor_atual - valor_alvo
    if diferenca <= valor_alvo * TOLERANCIA_DESCONTO:
        return {'itens': carrinho, 'valor_atual': valor_atual, 'diferenca': diferenca, 'valor_alvo': valor_alvo}
    return None

# --- INTERFACE ---
st.title("🛒 PDV Rápido - ECN Contabilidade")

aba_manual, aba_csv = st.tabs(["🛒 Venda Manual", "📥 Fechamento de Caixa (CSV)"])

# ABA 1: VENDA MANUAL
with aba_manual:
    st.write("Digite o valor total da venda e o sistema monta o carrinho.")
    valor_alvo = st.number_input("Valor Total da Venda (R$):", min_value=1.0, value=50.0, step=10.0)
    
    if 'carrinho_atual' not in st.session_state:
        st.session_state.carrinho_atual = None

    if st.button("🔍 Montar Carrinho", type="primary"):
        st.session_state.carrinho_atual = None
        produtos = buscar_produtos_com_estoque()
        if produtos:
            carrinho = montar_carrinho(valor_alvo, produtos)
            if carrinho:
                st.session_state.carrinho_atual = carrinho
            else:
                st.error("❌ DESCONTO RECUSADO: A diferença passa do limite permitido.")
        else:
            st.error("Não há produtos com estoque.")

    if st.session_state.carrinho_atual:
        dados = st.session_state.carrinho_atual
        st.success(f"✅ Carrinho montado! Desconto: R$ {dados['diferenca']:.2f}")
        
        for item in dados['itens']:
            st.write(f"📦 **{item['quantidade']:g}x** {item['descricao']} - R$ {item['preco_unitario']:.2f}")
        
        st.write(f"### 🎯 Total a Pagar: R$ {dados['valor_alvo']:.2f}")
        
        if st.button("🚀 Confirmar e Emitir NFC-e", type="primary", key="btn_emitir_manual"):
            with st.spinner("📡 Emitindo..."):
                status_code, retorno = emitir_nota_focus(dados['itens'], dados['diferenca'], dados['valor_alvo'])
                if status_code in [200, 201, 202] and retorno.get('status') == 'autorizado':
                    supabase.table('notas_emitidas').insert({
                        'cliente_cnpj': '57468706000108', 'numero_nota': retorno.get('numero'),
                        'serie': retorno.get('serie'), 'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                        'status': retorno.get('status'), 'valor_total': dados['valor_alvo'], 'json_retorno': retorno
                    }).execute()
                    for item in dados['itens']:
                        supabase.table('estoque_movimentacoes').insert({
                            'produto_id': item['id'], 'tipo_movimento': 'SAIDA',
                            'quantidade': item['quantidade'], 'valor_unitario': item['preco_unitario'], 'origem': 'VENDA_PAINEL'
                        }).execute()
                    st.balloons()
                    st.success("🎉 NOTA AUTORIZADA E ESTOQUE ATUALIZADO!")
                    st.write(f"**Chave:** `{retorno.get('chave_nfe')}`")
                    st.session_state.carrinho_atual = None
                else:
                    st.error(f"Erro: {retorno.get('mensagem_sefaz')}")

# ABA 2: FECHAMENTO DE CAIXA (Importação CSV)
with aba_csv:
    st.write("Faça o upload do relatório CSV da InfinityPay para emitir as notas do dia.")
    arquivo_csv = st.file_uploader("Selecione o arquivo CSV", type="csv")
    
    if arquivo_csv is not None:
        try:
            df = pd.read_csv(arquivo_csv, encoding='utf-8')
            
            # Filtra apenas as vendas Aprovadas
            df_vendas = df[df['Status'] == 'Aprovada']
            
            # Converte os valores para numérico (lidando com vírgula)
            df_vendas['Valor'] = df_vendas['Valor (R$)'].astype(str).str.replace('.', '').str.replace(',', '.').astype(float)
            
            st.success(f"📊 Arquivo lido com sucesso! Encontradas **{len(df_vendas)}** vendas aprovadas.")
            st.write(f"💰 Valor total das vendas: **R$ {df_vendas['Valor'].sum():.2f}**")
            
            st.divider()
            
            st.write("### Pronto para emitir?")
            st.write("Clique no botão abaixo para o sistema emitir todas as notas automaticamente.")
            
            # BOTÃO GRANDE E VISÍVEL
            if st.button("🚀 EMITIR TODAS AS NOTAS AGORA", type="primary", use_container_width=True):
                produtos = buscar_produtos_com_estoque()
                if not produtos:
                    st.error("Não há estoque para emitir.")
                else:
                    sucessos = 0
                    erros = 0
                    
                    progresso = st.progress(0)
                    status_texto = st.empty()
                    
                    # Limita a 15 notas por vez
                    vendas_para_emitir = df_vendas.head(15)
                    
                    for index, row in vendas_para_emitir.iterrows():
                        valor_venda = float(row['Valor'])
                        status_texto.write(f"⏳ Emitindo nota {index+1} de {len(vendas_para_emitir)} (R$ {valor_venda:.2f})...")
                        
                        carrinho = montar_carrinho(valor_venda, produtos)
                        if carrinho:
                            status_code, retorno = emitir_nota_focus(carrinho['itens'], carrinho['diferenca'], valor_venda)
                            if status_code in [200, 201, 202] and retorno.get('status') == 'autorizado':
                                supabase.table('notas_emitidas').insert({
                                    'cliente_cnpj': '57468706000108', 'numero_nota': retorno.get('numero'),
                                    'serie': retorno.get('serie'), 'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                                    'status': retorno.get('status'), 'valor_total': valor_venda, 'json_retorno': retorno
                                }).execute()
                                for item in carrinho['itens']:
                                    supabase.table('estoque_movimentacoes').insert({
                                        'produto_id': item['id'], 'tipo_movimento': 'SAIDA',
                                        'quantidade': item['quantidade'], 'valor_unitario': item['preco_unitario'], 'origem': 'VENDA_CSV'
                                    }).execute()
                                sucessos += 1
                            else:
                                erros += 1
                        else:
                            erros += 1
                        
                        progresso.progress((index + 1) / len(vendas_para_emitir))
                        time.sleep(2)
                    
                    status_texto.empty()
                    st.success(f"🏁 Finalizado! Sucessos: {sucessos} | Erros: {erros}")
                    if len(df_vendas) > 15:
                        st.warning("Limite de 15 notas por vez atingido. Rode novamente para o restante.")
                        
        except Exception as e:
            st.error(f"Erro ao ler o CSV: {e}")
