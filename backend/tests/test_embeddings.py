import math
from app.services.embeddings import cosine_similarity, ema_update


class TestCosineSimilarity:
    def test_identical(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


class TestEmaUpdate:
    def test_result_is_normalized(self):
        out = ema_update([1.0, 0.0], [0.0, 1.0], alpha=0.2)
        length = math.sqrt(sum(x * x for x in out))
        assert abs(length - 1.0) < 1e-6

    def test_moves_toward_new(self):
        # Starting at [1,0], nudging toward [0,1] should raise the 2nd component
        out = ema_update([1.0, 0.0], [0.0, 1.0], alpha=0.3)
        assert out[1] > 0.0

    def test_alpha_zero_keeps_old_direction(self):
        out = ema_update([1.0, 0.0], [0.0, 1.0], alpha=0.0)
        assert out[0] == 1.0 and out[1] == 0.0
