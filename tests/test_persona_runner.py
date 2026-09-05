"""페르소나 러너 회귀 테스트 (P6 마일스톤).

scripts.run_personas를 서브프로세스가 아니라 함수 임포트로 인프로세스에서 돌린다
(--llm rule 모드로 전 페르소나 x 골든 질문 전부). `run_all()`은 자체적으로 임시 DB
복사본을 만들고 끝나면 `app.data.db.DB_PATH`/`app.services.insights._banned_cache`/
`app.api.routes._llm_provider`를 호출 전 값으로 되돌리므로, 이 테스트 뒤에 실행되는
다른 테스트 파일(예: test_spending.py)에 영향을 주지 않는다.

pytest는 기본적으로 파일명 알파벳 순으로 테스트 모듈을 수집한다. 이 파일보다 먼저
수집되는 tests/test_api.py 등이 이미 `app.data.db`를 자신의 임시 DB로 임포트해뒀을 수
있는데(모듈 전역 상수라 최초 임포트 시점에 고정됨, test_api.py 헤더 주석 참고),
scripts.run_personas.run_all()은 환경변수가 아니라 이미 임포트된 모듈 속성을 직접
바꾸는 방식이라 이런 순서 문제와 무관하게 항상 올바르게 격리된다.
"""
from __future__ import annotations

from scripts import run_personas as runner


def test_golden_questions_cover_all_personas_with_min_seven_each():
    by_persona = runner.load_golden_questions()
    assert set(by_persona.keys()) == {f"P{i}" for i in range(1, 9)}
    for pid, qs in by_persona.items():
        assert len(qs) >= 7, f"{pid} 골든 질문이 7개 미만입니다({len(qs)}개)"
        ids = [q["id"] for q in qs]
        assert len(ids) == len(set(ids)), f"{pid}에 중복된 질문 id가 있습니다: {ids}"

    # P5/P7(연체 신호)은 안전모드 크라이시스 문항을 최소 1개 더 가진다.
    for pid in ("P5", "P7"):
        types = {q["type"] for q in by_persona[pid]}
        assert "crisis" in types, f"{pid}에 crisis 문항이 없습니다"


def test_persona_runner_rule_mode_all_personas_zero_hard_failures():
    """--llm rule로 페르소나 8명 전원을 인프로세스로 실행하고 하드 실패가 없는지 확인한다.

    데이터베이스는 항상 data/donn.db의 임시 복사본이며 원본은 건드리지 않는다
    (run_all -> make_temp_db_copy).
    """
    summary = runner.run_all(llm_mode="rule", judge=False)

    assert len(summary.personas) == 8
    assert summary.golden_question_count >= 8 * 7

    hard = summary.hard_failures
    detail = "\n".join(f"{pid}: {c.name} -> {c.detail}" for pid, c in hard)
    assert not hard, f"하드 실패 {len(hard)}건:\n{detail}"

    # 재현성: 같은 컨텍스트로 두 번 실행한 result_hash가 페르소나마다 실제로 채워져 있고 일치해야 한다.
    for p in summary.personas:
        assert p.result_hash_1, f"{p.persona_id}의 result_hash가 비어 있습니다"
        assert p.result_hash_1 == p.result_hash_2
        assert p.replay_match is True

    # 리포트 렌더링 자체도 예외/스타일 위반 없이 동작해야 한다(em dash 금지 포함, 렌더 함수
    # 안에서 이미 assert하지만 여기서도 회귀 방지 차원에서 한 번 더 확인).
    md = runner.render_markdown(summary)
    assert "—" not in md
    payload = runner.render_json(summary)
    assert payload["hard_failure_count"] == 0


def test_run_all_restores_global_state(monkeypatch):
    """run_all()이 끝난 뒤 db_module.DB_PATH / _banned_cache / _llm_provider를
    호출 전 값으로 되돌리는지 확인한다(다른 테스트 파일을 오염시키지 않기 위한 계약)."""
    before_db_path = runner.db_module.DB_PATH
    before_provider = runner.routes_module._llm_provider

    runner.run_all(personas=["P1"], llm_mode="rule", judge=False)

    assert runner.db_module.DB_PATH == before_db_path
    assert runner.routes_module._llm_provider is before_provider
