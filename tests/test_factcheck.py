from gundem.factcheck import factcheck, source_similarity


def test_unsupported_number_flagged():
    assert factcheck("3 ölü", "2 kişi yaralandı") == ["3"]


def test_supported_number_not_flagged_with_decimal_comma():
    source = "Enflasyon yüzde 2.5 arttı, 40 ilde ölçüm yapıldı."
    assert factcheck("Enflasyon yüzde 2,5 arttı; 40 ilde ölçüm var.", source) == []


def test_supported_name_not_flagged():
    source = "Galatasaray Fenerbahçe'yi 2-1 yendi. Maç İstanbul'da oynandı."
    text = "Derbide Galatasaray, Fenerbahçe karşısında 2-1 kazandı. Maç İstanbul'daki statta oynandı."
    assert factcheck(text, source) == []


def test_unsupported_name_flagged():
    source = "Galatasaray Fenerbahçe'yi 2-1 yendi."
    text = "Derbide Galatasaray, Beşiktaş'ı geride bıraktı."
    assert factcheck(text, source) == ["Beşiktaş"]


def test_sentence_start_short_words_and_credit_skipped():
    source = "Belediye yeni metro hattını açtı."
    text = "Yeni metro hattı açıldı! Hat bu sabah AB ile ilgili değil. Belediye duyurdu. (AA)"
    # "Yeni", "Hat", "Belediye" start sentences; "AB" is <= 2 chars; "(AA)" is the credit.
    assert factcheck(text, source, credit="(AA)") == []


def test_flags_unique_in_order_of_appearance():
    source = "Toplantı yapıldı."
    text = "Toplantıda Ahmet 5 madde açıkladı, Ahmet 5 maddeyi Zeynep ile 7 saatte yazdı."
    assert factcheck(text, source) == ["Ahmet", "5", "Zeynep", "7"]


def test_similarity_high_for_copied_text():
    source = "Belediye, şehir merkezindeki yeni metro hattının yarın sabah açılacağını duyurdu. Hat 12 durak içeriyor."
    text = "Belediye, şehir merkezindeki yeni metro hattının yarın sabah açılacağını duyurdu."
    assert source_similarity(text, source) > 0.9


def test_similarity_low_for_rewritten_text():
    source = "Belediye, şehir merkezindeki yeni metro hattının yarın sabah açılacağını duyurdu."
    text = "Raylı sistemde yeni dönem: merkeze giden hat için geri sayım bitiyor 🚇"
    assert source_similarity(text, source) < 0.7
