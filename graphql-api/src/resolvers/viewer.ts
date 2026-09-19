/**
 * Query.viewer and Viewer.sourceRecords - the two "structural" resolvers
 * from the design doc's Root shape. Query.viewer itself does no I/O: the
 * authenticated user + database were already derived in the `context`
 * function (buildContext), before this resolver even runs, so no field
 * anywhere accepts a user ID or database name as an argument.
 */
export const viewerResolvers = {
  Query: {
    viewer: () => ({}),
  },
  Viewer: {
    sourceRecords: () => ({}),
  },
};
