from llm import think_temizle


def test_think_temizle():
    assert think_temizle("<think>düşünce</think>\nCevap") == "Cevap"
    assert think_temizle("Cevap <think>kesik") == "Cevap"
    assert think_temizle("düşünce</think>\nCevap") == "Cevap"
    assert think_temizle("Cevap") == "Cevap" and think_temizle("") == ""
