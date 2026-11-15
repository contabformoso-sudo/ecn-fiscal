    st.divider()
    st.subheader("🛒 Carrinho Atual")
    
    if st.session_state.carrinho_manual:
        total_carrinho = 0.0
        # Usamos uma cópia da lista para podermos alterar os valores com segurança
        for i, item in enumerate(st.session_state.carrinho_manual):
            
            col_desc, col_preco, col_subtotal, col_rem = st.columns([3, 1.5, 1.5, 0.5])
            
            col_desc.write(f"**{item['quantidade']:g}x** {item['descricao']}")
            
            # PERMITE ALTERAR O PREÇO UNITÁRIO NO CARRINHO
            novo_preco = col_preco.number_input(
                "Preço Unit. (R$)", 
                value=float(item['preco_unitario']), 
                step=1.0, 
                key=f"preco_carrinho_{item['id']}",
                label_visibility="collapsed"
            )
            
            # Se o preço mudou, atualiza o item e o subtotal
            if novo_preco != item['preco_unitario']:
                st.session_state.carrinho_manual[i]['preco_unitario'] = novo_preco
                st.session_state.carrinho_manual[i]['subtotal'] = item['quantidade'] * novo_preco
                st.rerun() # Recarrega para atualizar o total
                
            col_subtotal.write(f"R$ {item['subtotal']:.2f}")
            
            if col_rem.button("❌", key=f"rem_{i}"):
                st.session_state.carrinho_manual.pop(i)
                st.rerun()
            
            total_carrinho += item['subtotal']
        
        st.write(f"### Total: R$ {total_carrinho:.2f}")
        
        if st.button("🚀 Emitir NFC-e do Carrinho", type="primary", use_container_width=True):
            # O resto do código de emissão continua igual...
