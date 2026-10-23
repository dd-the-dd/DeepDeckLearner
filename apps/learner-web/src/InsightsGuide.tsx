export default function InsightsGuide() {
  return <details className="insights-guide" open>
    <summary>What does the 95% interval mean?</summary>
    <p>The win rate describes the matches recorded here. The interval shows how uncertain that estimate is with this sample.</p>
    <p>For example, 10 wins in 20 matches gives a 50% win rate, but an estimated range of about 30–70%. More comparable matches usually narrow the range.</p>
    <p><strong>It does not mean a 95% chance of winning the next match.</strong> Different opponents, decks and table sizes can change the result. Compare like with like.</p>
  </details>;
}
