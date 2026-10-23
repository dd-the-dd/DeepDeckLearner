export type CardSearchFilters = {
  format:'legacy'|'commander'; query:string; oracle_contains:string; semantic_text:string;
  types:string[]; color_identity:string; identity_match:'within'|'exact'; commander_only:boolean;
  min_mana_value?:number; max_mana_value?:number; color_count?:number;
};
export type PreparedCardSearch = {filters:CardSearchFilters; deckId:string; result?:import('./api').CardResearch};
let latest:PreparedCardSearch|null=null;
export function preparedCardSearch(deckId:string, format:string):PreparedCardSearch|null {
  return latest && latest.filters.format===format && latest.deckId===deckId ? latest : null;
}
export function publishCardSearch(result:unknown, deckId:string):boolean {
  if(!result || typeof result!=='object') return false;
  const value=result as {filters?:CardSearchFilters;items?:unknown[]};
  const filters=value.filters;
  if(!filters || !['legacy','commander'].includes(filters.format) || typeof filters.query!=='string' || !Array.isArray(filters.types)) return false;
  latest={filters,deckId,result:Array.isArray(value.items)?result as import('./api').CardResearch:undefined};
  window.dispatchEvent(new CustomEvent('deepdeck:card-search-filters',{detail:latest}));
  return true;
}
