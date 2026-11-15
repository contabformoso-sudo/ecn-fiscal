"""PDV — venda rápida por valor.

O operador digita quanto o cliente vai pagar; o sistema sugere os itens,
mostra tudo e só emite depois da confirmação. A baixa de estoque é feita pelo
custo real (PEPS), nunca pelo preço de venda.
"""

import streamlit as st

from core import db
from core.carrinho import (
    StatusCarrinho,
    catalogo_vendavel,
    montar_carrinho,
    tolerancia_configurada,
)
from core.estoque import Estoque
from core.vendas import VendaError, finalizar_venda

st.set_page_config(page_title="PDV ECN - Emissão de NFC-e", page_icon="🧾", layout="centered")

CAMPOS_PRODUTO = "id, descricao, preco_venda, ncm, ean, sujeito_st"


@st.cache_data(ttl=60)
def produtos_vendaveis() -> list[dict]:
    """Catálogo com saldo — uma consulta só, em vez de uma por produto."""
    estoque = Estoque.carregar()
    produtos = db.supabase.table("produtos").select(CAMPOS_PRODUTO).execute().data
    return catalogo_vendavel(produtos, estoque)


st.title("🛒 PDV Rápido - Venda por Valor")
st.write("Digite o valor total da venda e o sistema monta o carrinho automaticamente.")
st.caption(
    "O sistema junta os itens até chegar no valor digitado. Se passar um pouco, "
    f"o excedente vira desconto — até {tolerancia_configurada():.0%}. "
    "Passando disso, a busca é refeita."
)

valor_alvo = st.number_input("Valor Total da Venda (R$):", min_value=1.0, value=50.0, step=10.0)

if "carrinho_atual" not in st.session_state:
    st.session_state.carrinho_atual = None

if st.button("🔍 Montar Carrinho", type="primary"):
    st.session_state.carrinho_atual = None
    produtos = produtos_vendaveis()

    if not produtos:
        st.error("Não há produtos com estoque no sistema.")
    else:
        carrinho = montar_carrinho(produtos, valor_alvo)
        if carrinho.status is StatusCarrinho.OK:
            st.session_state.carrinho_atual = carrinho
        elif carrinho.status is StatusCarrinho.ABAIXO:
            st.warning("A soma ficou abaixo do valor alvo. Tente novamente.")
        else:
            st.error(f"❌ DESCONTO RECUSADO: {carrinho.mensagem}")

carrinho = st.session_state.carrinho_atual
if carrinho:
    if carrinho.desconto > 0:
        st.success(f"✅ Carrinho montado! Desconto de R$ {carrinho.desconto:.2f} aplicado.")
    else:
        st.success("✅ Carrinho montado! Fechou exatamente no valor.")

    st.write("### 🛍️ Itens da Nota:")
    for item in carrinho.itens:
        st.write(
            f"📦 **{item.quantidade:g}x** {item.descricao} - "
            f"R$ {item.preco_unitario:.2f} (Subtotal: R$ {item.subtotal:.2f})"
        )

    st.write(f"**Soma dos Produtos:** R$ {carrinho.soma:.2f}")
    st.write(f"**Desconto:** - R$ {carrinho.desconto:.2f}")
    st.write(f"### 🎯 Total a Pagar: R$ {carrinho.valor_final:.2f}")

    st.divider()

    if st.button("🚀 Confirmar e Emitir NFC-e", type="primary"):
        with st.spinner("📡 Conectando à SEFAZ e emitindo a nota..."):
            try:
                resultado = finalizar_venda(
                    carrinho.itens, carrinho.desconto, carrinho.valor_final
                )
            except VendaError as erro:
                st.error(f"❌ {erro}")
                resultado = None

        if resultado is not None:
            if resultado.autorizada:
                produtos_vendaveis.clear()
                st.balloons()
                st.success("🎉 NOTA AUTORIZADA E ESTOQUE ATUALIZADO COM SUCESSO!")
                st.write(f"**Chave de Acesso:** `{resultado.chave}`")
                if resultado.danfe:
                    st.markdown(f"[📄 Clique aqui para baixar o DANFE (PDF)]({resultado.danfe})")

                for aviso in resultado.avisos:
                    st.warning(f"⚠️ {aviso}")

                st.session_state.carrinho_atual = None
            elif resultado.status_http in (200, 201, 202):
                st.warning(f"⚠️ Nota processada, mas com status: {resultado.retorno.get('status')}")
                st.write(f"Motivo: {resultado.retorno.get('mensagem_sefaz')}")
            else:
                st.error("❌ Erro na emissão. Verifique os detalhes abaixo:")
                st.json(resultado.retorno)
