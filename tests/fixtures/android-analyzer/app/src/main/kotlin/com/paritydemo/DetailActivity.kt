package com.paritydemo.ui

import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.ViewModel
import androidx.fragment.app.Fragment

class DetailActivity : AppCompatActivity() {
    override fun onStart() {
        super.onStart()
        render()
    }

    private fun render() {
        bindView()
    }

    fun bindView() {
        // touch the resource system so graph consumers can audit it
        val id = R.id.detail_container
    }
}

class DetailViewModel : ViewModel() {
    fun load(userId: String) {
        fetch(userId)
    }

    private fun fetch(userId: String) {
        repositoryLookup(userId)
    }
}

class DeepLinkFragment : Fragment() {
    fun onVisible() {
        DetailViewModel().load("7")
    }
}
