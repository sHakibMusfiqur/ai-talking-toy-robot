try {
  rs.status();
  if (db.hello().isWritablePrimary) {
    quit(0);
  } else {
    quit(1);
  }
} catch (e) {
  try {
    rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]});
  } catch (e2) {
  }
  quit(1);
}
