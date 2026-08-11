# Report extraction evaluation

Run the synthetic format smoke check:

```bash
uv run genesis-evidence-evaluate \
  evals/report-gold.synthetic.jsonl \
  evals/report-predictions.synthetic.jsonl
```

The output quantifies row precision/recall/F1, value, unit and reference-range
accuracy, source-evidence coverage, abnormal-row recall, unsafe inclusion,
file order and subject consistency.

The committed sample is synthetic and only validates the evaluator. It is not
a product-accuracy claim. Put real de-identified report files, gold labels and
model predictions below ignored `evals/private/`; review redaction before use
and report the case count with every accuracy result.
