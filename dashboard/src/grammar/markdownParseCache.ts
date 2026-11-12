import type { Processor } from 'unified';
import type { TaskDocumentResources } from '../data/taskDocuments';

/** Keep syntax data only. Later plugins may mutate their own clone. */
export function remarkCachedParse(this: Processor, resources: TaskDocumentResources): void {
  const parse = this.parser;
  if (!parse) throw new Error('Markdown cache requires the configured native parser');
  this.parser = (text, file) =>
    structuredClone(resources.prepare(`markdown:remark-gfm4:${text}`, () => parse(text, file)));
}
