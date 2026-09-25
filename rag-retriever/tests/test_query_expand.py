from rag_retriever.query_expand import keyword_variants


def test_variants_drop_function_words_and_keep_content():
    variants = keyword_variants("合同 的 违约金 是 多少")
    assert variants
    assert all("的" not in v.split() for v in variants)
    assert any("违约金" in v for v in variants)


def test_variants_empty_for_blank():
    assert keyword_variants("   ") == []


def test_variants_at_most_two_and_never_the_original_query():
    query = "合同 的 违约金 是 多少"
    variants = keyword_variants(query)
    assert 1 <= len(variants) <= 2
    assert query not in variants
    assert keyword_variants(query).count(variants[0]) == 1


def test_variants_drop_single_han_characters():
    assert keyword_variants("甲 的 违约金") == ["违约金"]


def test_variants_skip_sequence_that_matches_original_tokens():
    # Nothing to drop, and fewer than three content words: no second variant.
    assert keyword_variants("违约金 合同") == []


def test_variants_second_is_two_longest_content_words():
    # First variant would repeat the original token sequence, so only the pair remains.
    variants = keyword_variants("甲方 乙方 违约金")
    assert variants == ["违约金 甲方"]
    kept = keyword_variants("合同 的 违约金 是 多少")
    assert kept[0] == "合同 违约金 多少"
    assert kept[1] == "违约金 合同"
