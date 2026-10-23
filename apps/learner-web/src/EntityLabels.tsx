export const parseLabels = (text: string) => [...new Set(text.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))];
export const supportsEntityLabels = (entity: {tags?: string[]; collections?: string[]}) => Array.isArray(entity.tags) && Array.isArray(entity.collections);

export default function EntityLabels({ tags, collections, onChange }: {
  tags: string; collections: string; onChange: (field: 'tags' | 'collections', value: string) => void;
}) {
  return <div className="entity-labels"><label>Tags (comma separated)<input value={tags} maxLength={2000} onChange={(event) => onChange('tags', event.target.value)} placeholder="tempo, favorites" /></label><label>Collections (comma separated)<input value={collections} maxLength={2000} onChange={(event) => onChange('collections', event.target.value)} placeholder="Friday night, test pool" /></label><small>Organize this deck or agent and choose a filtered Random pool in Play. These labels are separate from card strategy tags.</small></div>;
}
