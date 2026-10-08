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


def test_clean_passage_stops_bare_url_before_cjk_sentence():
    raw = "参见https://example.invalid/a。第3条应付款50,000.00元"
    assert clean_passage(raw) == "参见。第3条应付款50,000.00元"


def test_clean_passage_keeps_amount_inside_angle_brackets():
    raw = "借款<50,000.00元，第3条见后续>"
    assert clean_passage(raw) == raw
