package smoke;

/** Minimal interface + concrete same-site stub for pass3 supertype-walk dedup (PR-1). */
@interface Repository {
}

@Repository
interface JpaStyleRepo {
    void save(Object entity);
}

@Repository
class JpaStyleRepoImpl implements JpaStyleRepo {
    @Override
    public void save(Object entity) {
    }
}

public class SupertypeDedupPatterns {
    private final JpaStyleRepoImpl repo;

    SupertypeDedupPatterns(JpaStyleRepoImpl repo) {
        this.repo = repo;
    }

    void persist(Object entity) {
        repo.save(entity);
    }
}
