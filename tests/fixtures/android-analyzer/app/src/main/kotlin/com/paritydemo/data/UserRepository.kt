package com.paritydemo.data

/**
 * Repository + Retrofit-style service contract.
 */
interface UserApi {
    @retrofit2.http.GET("users/{id}")
    fun user(id: String): User

    @retrofit2.http.POST("users")
    suspend fun createUser(user: User): User
}

data class User(val id: String, val name: String)

class UserRepository {
    private val api: UserApi = ServiceModule.provideUserApi()

    fun displayName(userId: String): String {
        val cached = lookup(userId)
        if (cached != null) {
            return cached
        }
        return api.user(userId).name
    }

    private fun lookup(userId: String): String? = null

    fun watchUpdates(callback: (User) -> Unit) {
        val listener = { user: User -> callback(user) }
        notify(listener)
    }

    fun wireAll() {
        val handler = android.os.Handler()
        handler.post({ flush() })
    }

    private fun flush() {
    }

    private fun notify(callback: (User) -> Unit) {
    }
}

object ServiceModule {
    fun provideUserApi(): UserApi {
        return retrofit().create(UserApi::class.java)
    }

    private fun retrofit(): retrofit2.Retrofit {
        return retrofit2.Retrofit.Builder().build()
    }
}

enum class SyncState {
    IDLE, RUNNING, FAILED
}

sealed class SyncResult {
    object Idle : SyncResult()
    data class Done(val user: User) : SyncResult()
}

abstract class BaseWorker {
    abstract fun work()
}

class SyncWorker : BaseWorker() {
    override fun work() {
        ServiceModule.provideUserApi()
    }
}
