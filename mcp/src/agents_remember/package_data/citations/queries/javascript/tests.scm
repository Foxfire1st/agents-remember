; Call-site capture for JavaScript/JSX test-case and suite expressions.
; Captured call nodes feed the test_cases / test_case_ranges policy layer.

(call_expression
  function: [
    (identifier)
    (member_expression)
    (call_expression)
  ]) @test.call
