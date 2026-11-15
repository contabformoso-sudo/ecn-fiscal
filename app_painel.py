import streamlit as st
import os
import random
import requests
import json
import base64
import time
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
        if p.get('preco_venda') is None or p['preco_venda'] <= 0:
            continue
            
        movs = supabase.table('estoque_movimentacoes').select('tipo_movimento, quantidade').eq('produto_id', p['id']).execute()
        saldo = 0
        for m in movs.data:
            if m['tipo_movimento'] == 'ENTRADA':
                saldo += float(m['quantidade'])
            else:
                saldo -= float(m['quantidade'])
                
        if saldo > 0:
            produtos_com_estoque.append({
                'id': p['id'],
                'descricao': p['descricao'],
                'preco': float(p['preco_venda']),
                'saldo': saldo,
                'ncm': p.get('ncm') or '61091000',
                'ean': p.get('ean') or 'SEM GTIN',
                'sujeito_st': p.get('sujeito_st', False)
            })
    return produtos_com_estoque

def emitir_nota_focus(carrinho, valor_desconto, valor_total):
    fuso_brasilia = timezone(timedelta(hours=-3))
    data_agora = datetime.now(fuso_brasilia).strftime("%Y-%m-%dT%H:%M:%S-03:00")
    
    items_json = []
    for index, item in enumerate(carrinho):
        if item.get('sujeito_st'):
            cfop_saida = "5405"
            csosn_saida = "500"
        else:
            cfop_saida = "5102"
            csosn_saida = "102"

        item_json = {
            "numero_item": str(index + 1),
            "codigo_produto": str(item['id'])[:10], 
            "descricao": item['descricao'],
            "cfop": cfop_saida,
            "unidade_comercial": "UN",
            "quantidade_comercial": float(item['quantidade']),
            "valor_unitario_comercial": float(item['preco_unitario']),
            "valor_bruto": float(item['subtotal']),
            "codigo_ncm": item['ncm'],
            "codigo_ean": item['ean'],
            "icms_situacao_tributaria": csosn_saida,
            "icms_origem": "0",
            "pis_situacao_tributaria": "07",
            "cofins_situacao_tributaria": "07"
        }

        if csosn_saida == "500":
            item_json["valor_icms_st"] = 0.00
            item_json["valor_bc_icms_st"] = 0.00

        items_json.append(item_json)

    payload = {
        "natureza_operacao": "Venda ao Consumidor",
        "data_emissao": data_agora,
        "tipo_documento": 1,
        "finalidade_emissao": 1,
        "consumidor_final": 1,
        "presenca_comprador": 1,
        "modalidade_frete": "9", 
        "cnpj_emitente": "57468706000108", 
        "destinatario": {"cpf": "12345678909", "nome": "CONSUMIDOR TESTE HOMOLOGACAO"},
        "items": items_json,
        "desconto": float(valor_desconto),
        "pagamentos": [{"forma_pagamento": "01", "valor_pagamento": float(valor_total)}]
    }

    headers = {"Authorization": f"Basic {auth_encoded}", "Content-Type": "application/json"}
    ref_nota = f"teste_{int(time.time())}" 
    url_focus = f"https://homologacao.focusnfe.com.br/v2/nfce?ref={ref_nota}"

    try:
        response = requests.post(url_focus, json=payload, headers=headers)
        return response.status_code, response.json()
    except Exception as e:
        return 500, {"erro": str(e)}

st.title("🛒 PDV Rápido - Venda por Valor")
st.write("Digite o valor total da venda e o sistema monta o carrinho automaticamente.")

valor_alvo = st.number_input("Valor Total da Venda (R$):", min_value=1.0, value=50.0, step=10.0)

if 'carrinho_atual' not in st.session_state:
    st.session_state.carrinho_atual = None

if st.button("🔍 Montar Carrinho", type="primary"):
    st.session_state.carrinho_atual = None
    produtos = buscar_produtos_com_estoque()
    
    if not produtos:
        st.error("Não há produtos com estoque no sistema.")
    else:
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
        desconto_maximo = valor_alvo * TOLERANCIA_DESCONTO
        
        if diferenca <= 0:
            st.warning("A soma ficou abaixo do valor alvo. Tente novamente.")
        elif diferenca <= desconto_maximo:
            st.session_state.carrinho_atual = {'itens': carrinho, 'valor_atual': valor_atual, 'diferenca': diferenca, 'valor_alvo': valor_alvo}
        else:
            st.error(f"❌ DESCONTO RECUSADO: A diferença é R$ {diferenca:.2f}, mas o limite é R$ {desconto_maximo:.2f}.")

if st.session_state.carrinho_atual:
    dados = st.session_state.carrinho_atual
    st.success(f"✅ Carrinho montado! Desconto de R$ {dados['diferenca']:.2f} aplicado.")
    
    for item in dados['itens']:
        st.write(f"📦 **{item['quantidade']:g}x** {item['descricao']} - R$ {item['preco_unitario']:.2f} (Subtotal: R$ {item['subtotal']:.2f})")
    
    st.write(f"**Soma dos Produtos:** R$ {dados['valor_atual']:.2f}")
    st.write(f"**Desconto:** - R$ {dados['diferenca']:.2f}")
    st.write(f"### 🎯 Total a Pagar: R$ {dados['valor_alvo']:.2f}")
    
    st.divider()
    
    if st.button("🚀 Confirmar e Emitir NFC-e", type="primary"):
        with st.spinner("📡 Conectando à SEFAZ e emitindo a nota..."):
            status_code, retorno = emitir_nota_focus(dados['itens'], dados['diferenca'], dados['valor_alvo'])
            
            if status_code in [200, 201, 202]:
                if retorno.get('status') == 'autorizado':
                    try:
                        supabase.table('notas_emitidas').insert({
                            'cliente_cnpj': '57468706000108',
                            'numero_nota': retorno.get('numero'),
                            'serie': retorno.get('serie'),
                            'chave_acesso': retorno.get('chave_nfe', '').replace('NFe', ''),
                            'status': retorno.get('status'),
                            'valor_total': dados['valor_alvo'],
                            'json_retorno': retorno
                        }).execute()

                        for item in dados['itens']:
                            supabase.table('estoque_movimentacoes').insert({
                                'produto_id': item['id'],
                                'tipo_movimento': 'SAIDA',
                                'quantidade': item['quantidade'],
                                'valor_unitario': item['preco_unitario'],
                                'origem': 'VENDA_PAINEL'
                            }).execute()
                        
                        st.balloons()
                        st.success("🎉 NOTA AUTORIZADA E ESTOQUE ATUALIZADO COM SUCESSO!")
                        st.write(f"**Chave de Acesso:** `{retorno.get('chave_nfe')}`")
                        url_danfe = f"https://homologacao.focusnfe.com.br{retorno.get('caminho_danfe')}"
                        st.markdown(f"[📄 Clique aqui para baixar o DANFE (PDF)]({url_danfe})")
                        st.session_state.carrinho_atual = None
                    except Exception as e:
                        st.warning(f"Nota autorizada, mas erro ao salvar no banco: {e}")
                else:
                    st.warning(f"⚠️ Nota processada, mas com status: {retorno.get('status')}")
                    st.write(f"Motivo: {retorno.get('mensagem_sefaz')}")
            else:
                st.error("❌ Erro na emissão. Verifique os detalhes abaixo:")
                st.json(retorno)
