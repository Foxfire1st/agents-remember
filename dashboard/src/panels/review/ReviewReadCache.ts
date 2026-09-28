// What the reviewer has already read for the comparison on screen, so returning to a subject or
// reopening a file costs no request.
//
// WHAT IS KEPT, AND UNDER WHICH KEY
//
//   * A subject's whole review, under the read cycle's own question key (task context, record,
//     subject). Paged questions are not kept: a roster walk is a continuation of the retained page and
//     stays owned by the read cycle.
//   * A changed file's content, under exactly the fields its request names (task context, path and
//     the two code tree ids). One answer carries both sides, so the side needs no key of its own.
//   * Only answers. A refusal or a failure is asked again, because it may describe a transient state
//     rather than the immutable comparison.
//
// WHEN IT IS DROPPED
//
// Every kept review carries the comparison generation it was read under: the pair of code trees and,
// when knowledge was compared, the pair of knowledge snapshots. An answer from another generation (a
// refresh that reached a new candidate publication) empties the whole cache before it is kept, so a
// subject the reader returns to is never one read against a comparison the surface has left. The
// reader's refresh forgets the question it re-asks, so a refresh always reaches the server.
//
// BOUNDS. Both stores are capped and evict the least recently used entry, and the cache belongs to
// one mounted review surface: it is reclaimed with it. It is a working surface, never a record.

import { createContext } from 'react';

import type { ReviewPayload, ReviewSourceContentResult } from '../../data/review';

export const REVIEW_CACHE_LIMIT = 24;
export const SOURCE_CACHE_LIMIT = 32;

class BoundedStore<V> {
  private readonly entries = new Map<string, V>();

  constructor(private readonly limit: number) {}

  get(key: string): V | undefined {
    const value = this.entries.get(key);
    if (value === undefined) return undefined;
    // Reading refreshes recency: the entry moves to the end of the insertion order.
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  set(key: string, value: V): void {
    this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  delete(key: string): void {
    this.entries.delete(key);
  }

  clear(): void {
    this.entries.clear();
  }

  get size(): number {
    return this.entries.size;
  }
}

export interface ComparisonGeneration {
  trees: string;
  snapshots?: string;
}

function comparisonGenerationOf(payload: ReviewPayload): ComparisonGeneration {
  const inventory = payload.source.inventory;
  const comparison = payload.comparison;
  return {
    trees: `${inventory.before_code_tree_id ?? ''}:${inventory.after_code_tree_id ?? ''}`,
    snapshots: comparison?.knowledge_compared
      ? `${comparison.before_snapshot_digest ?? ''}:${comparison.after_snapshot_digest ?? ''}`
      : undefined,
  };
}

// Two generations are different comparisons when their code trees differ, or when both compared
// knowledge and their snapshot pairs differ. A task-context answer compares no knowledge, so it says
// nothing about the snapshot pair and cannot move it.
export function sameGeneration(known: ComparisonGeneration, next: ComparisonGeneration): boolean {
  if (known.trees !== next.trees) return false;
  return known.snapshots === undefined || next.snapshots === undefined
    ? true
    : known.snapshots === next.snapshots;
}

export function sourceContentKey(
  repo: string,
  master: string,
  leaf: string,
  path: string,
  beforeCodeTreeId: string,
  afterCodeTreeId: string,
): string {
  return JSON.stringify([repo, master, leaf, path, beforeCodeTreeId, afterCodeTreeId]);
}

export class ReviewReadCache {
  private readonly reviews = new BoundedStore<ReviewPayload>(REVIEW_CACHE_LIMIT);
  private readonly sources = new BoundedStore<ReviewSourceContentResult>(SOURCE_CACHE_LIMIT);
  // The generation every kept entry was read under; a different one empties both stores.
  private generation: ComparisonGeneration | null = null;

  review(key: string): ReviewPayload | undefined {
    return this.reviews.get(key);
  }

  // Called for every admitted answer, kept or not: a paged or refreshed answer that reached another
  // generation must still drop what was read under the old one.
  observe(payload: ReviewPayload): void {
    const next = comparisonGenerationOf(payload);
    const known = this.generation;
    if (known !== null && !sameGeneration(known, next)) {
      this.reviews.clear();
      this.sources.clear();
      this.generation = next;
      return;
    }
    this.generation = { trees: next.trees, snapshots: next.snapshots ?? known?.snapshots };
  }

  keepReview(key: string, payload: ReviewPayload): void {
    this.observe(payload);
    this.reviews.set(key, payload);
  }

  forgetReview(key: string): void {
    this.reviews.delete(key);
  }

  source(key: string): ReviewSourceContentResult | undefined {
    return this.sources.get(key);
  }

  keepSource(key: string, result: ReviewSourceContentResult): void {
    if (result.state === 'content' && result.expansion) this.sources.set(key, result);
  }

  get sizes(): { reviews: number; sources: number } {
    return { reviews: this.reviews.size, sources: this.sources.size };
  }
}

// The surface provides its cache to the source content it renders; content rendered outside a review
// surface has none and reads every time, as before.
export const ReviewReadCacheContext = createContext<ReviewReadCache | null>(null);
