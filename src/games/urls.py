from rest_framework.routers import SimpleRouter

from games.views import GameViewSet

router = SimpleRouter()
router.register("games", GameViewSet, basename="game")

urlpatterns = router.urls
