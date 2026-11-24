# --- ABA 1: VENDA DETALHADA ---
with aba_detalhada:
    st.subheader("Adicione os itens um por um")
    
    if 'carrinho_manual' not in st.session_state:
        st.session_state.carrinho_manual = []

    produtos_db = buscar_produtos_com_estoque(ID_CLIENTE)
    
    if produtos_db:
        termo_busca = st.text_input("🔎 Buscar produto por nome ou código:", placeholder="Ex: cimento, pijama...")
        
        # AQUI FOI ADICIONADO O [:5] PARA LIMITAR A 5 ITENS
        produtos_filtrados = [p for p in produtos_db if termo_busca.lower() in p['descricao'].lower() or termo_busca in (p['ean'] or '')][:5]
        
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
            st.info("Nenhum produto encontrado com essa busca. Digite um termo mais específico.")

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
                        'status': retorno.get('status'), 'valor_total': total_carrinho, 'json_retorno': retorno,
                        'ambiente': 'producao'
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
