import { Component, type ReactNode } from 'react';
import { reportLocalRenderFailure } from './api';
import './app-recovery.css';

/** No game actions or exception details: just a categorical marker to the local controller. */
export default class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean; reference: string }> {
  state = { failed: false, reference: '' };
  private epoch = 0;
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() {
    const current = ++this.epoch;
    this.setState({ reference: '' });
    void reportLocalRenderFailure().then((result) => {
      if (current === this.epoch && result.recorded && /^[0-9a-f-]{36}$/i.test(result.reference)) this.setState({ reference: result.reference });
    }).catch(() => { /* Recovery remains available if the local journal cannot be reached. */ });
  }
  componentWillUnmount() { this.epoch++; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="app-recovery" role="alert">
      <section>
        <p className="app-recovery-brand">DeepDeck Learner</p>
        <h1>Let’s get you back to the table.</h1>
        <p>This screen could not be displayed. This recovery screen does not delete your saved agents, decks or replays.</p>
        <p>Unsaved edits may need to be entered again. No game, League registration or other action will be repeated automatically by these recovery controls.</p>
        <div className="app-recovery-actions">
          <button type="button" onClick={() => { this.epoch++; this.setState({ failed: false, reference: '' }); }}>Try displaying the app again</button>
          <button type="button" onClick={() => window.location.reload()}>Reload the app</button>
        </div>
        {this.state.reference && <p>Local error reference: <code>{this.state.reference}</code></p>}
        <p className="app-recovery-help">If it happens again, note the time and what you clicked before contacting <a href="https://www.deepdeckleague.com/support" target="_blank" rel="noopener noreferrer">DeepDeck support</a>. Do not share API keys, passwords or sign-in links. Only a render-error marker—not exception text—is recorded by your local controller. No report is uploaded to the League.</p>
      </section>
    </main>;
  }
}
