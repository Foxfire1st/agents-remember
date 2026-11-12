import { expect,it } from 'vitest';
import { chatLeaf,chatMaster } from '../test/fixtures/documentChat';
import { qualifiedLeafKey,sameTaskDocumentRef } from './taskIdentity';
import { taskEntrySelectionKey } from './taskReference';
it('keeps repository qualification in selection and role-chat identity', () => {
  if (!chatLeaf.reference) throw new Error('Fixture has no leaf document reference');
  expect(taskEntrySelectionKey(chatLeaf.key)).toBe('taskdoc:repo/master/leaf.json');
  expect(chatMaster.key).toBe('repo/master/task.json');
  expect(qualifiedLeafKey(chatLeaf)).toBe('repo/master/LEAF');
  expect(sameTaskDocumentRef(chatLeaf.reference, { ...chatLeaf.reference })).toBe(true);
  expect(
    sameTaskDocumentRef(chatLeaf.reference, { ...chatLeaf.reference, repository: 'other' }),
  ).toBe(false);
  expect(sameTaskDocumentRef(undefined, undefined)).toBe(false);
});
