//! Golden parity test cho `query_understanding` (nguồn: `query_understanding.py`).

use cortex_retrieval::query_understanding::QueryUnderstanding;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    from_text: Vec<TextCase>,
    from_paragraph: Vec<ParagraphCase>,
}

#[derive(Deserialize)]
struct TextCase {
    text: String,
    expected: Expected,
}

#[derive(Deserialize)]
struct ParagraphCase {
    text: String,
    max_chars: usize,
    expected: Expected,
}

#[derive(Deserialize)]
struct Expected {
    raw_query: String,
    intent: String,
    entities: Vec<String>,
    keywords: Vec<String>,
    actions: Vec<String>,
    domain_signals: Vec<String>,
    embedding_text: String,
}

fn assert_matches(u: &QueryUnderstanding, e: &Expected, context: &str) {
    assert_eq!(u.raw_query, e.raw_query, "{context} raw_query");
    assert_eq!(u.intent, e.intent, "{context} intent");
    assert_eq!(u.entities, e.entities, "{context} entities");
    assert_eq!(u.keywords, e.keywords, "{context} keywords");
    assert_eq!(u.actions, e.actions, "{context} actions");
    assert_eq!(u.domain_signals, e.domain_signals, "{context} domain_signals");
    assert_eq!(u.embedding_text, e.embedding_text, "{context} embedding_text");
}

#[test]
fn golden_parity_against_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/qu_golden.json")).expect("fixture hợp lệ");

    for case in &fixture.from_text {
        let u = QueryUnderstanding::from_text(&case.text);
        assert_matches(&u, &case.expected, &format!("from_text {:?}", case.text));
    }

    for case in &fixture.from_paragraph {
        let u = QueryUnderstanding::from_paragraph(&case.text, case.max_chars);
        assert_matches(
            &u,
            &case.expected,
            &format!("from_paragraph max_chars={}", case.max_chars),
        );
    }
}
