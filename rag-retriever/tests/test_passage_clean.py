from rag_retriever.passage_clean import clean_passage


def test_clean_passage_keeps_amount_and_heading_words():
    raw = "\n".join([
        "# 标题",
        "**50,000.00**",
        "![scan](images/p1.png)",
        "<b>被告</b>",
        "| --- | --- |",
        "https://example.invalid/a",
        "第3条 应付款",
    ])
    cleaned = clean_passage(raw)
    assert "50,000.00" in cleaned
    assert "标题" in cleaned
    assert "第3条" in cleaned
    assert "被告" in cleaned
    assert "#" not in cleaned
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "<b>" not in cleaned
    assert "---" not in cleaned
    assert "images/p1.png" not in cleaned
