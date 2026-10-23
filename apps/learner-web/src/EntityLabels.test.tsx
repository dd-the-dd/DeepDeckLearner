import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import EntityLabels, { parseLabels, supportsEntityLabels } from './EntityLabels';

describe('entity labels compatibility', () => {
  it('requires both explicit arrays before allowing label editing', () => {
    expect(supportsEntityLabels({})).toBe(false);
    expect(supportsEntityLabels({tags:[]})).toBe(false);
    expect(supportsEntityLabels({tags:[],collections:[]})).toBe(true);
    render(supportsEntityLabels({}) ? <EntityLabels tags="" collections="" onChange={()=>undefined} /> : <p>Restart the app to enable tags and collections.</p>);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText(/Restart the app/)).toBeTruthy();
  });
  it('normalizes personal labels', () => {
    expect(parseLabels(' Tempo,tempo, Friday ')).toEqual(['tempo','friday']);
  });
});
